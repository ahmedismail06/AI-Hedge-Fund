"""
Exposure Tracker — portfolio exposure computation and regime-gated limit checks.

This module is a pure-logic layer: it accepts positions as plain dicts (as returned
by Supabase), computes gross/net exposure fractions, and evaluates whether a proposed
new position would breach the regime-specific caps defined in REGIME_CAPS.

No Supabase calls are made here — callers supply the positions list and the portfolio
value scalar. The regime is embedded inside ExposureState (read upstream and passed in).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Optional

from dotenv import load_dotenv

load_dotenv()

if TYPE_CHECKING:
    from backend.portfolio.schemas import ExposureState

logger = logging.getLogger(__name__)

# ── Regime exposure caps ───────────────────────────────────────────────────────
# max_gross     : abs(long) + abs(short) as fraction of portfolio_value
# max_net_long  : (long - short) upper bound (positive = net long)
# max_net_short : (long - short) lower bound (negative = net short)

REGIME_CAPS: dict[str, dict[str, float]] = {
    "Risk-On":      {"max_gross": 1.50, "max_net_long":  0.50, "max_net_short":  0.00},
    "Transitional": {"max_gross": 1.20, "max_net_long":  0.20, "max_net_short": -0.20},
    "Risk-Off":     {"max_gross": 0.80, "max_net_long":  0.10, "max_net_short": -0.10},
    "Stagflation":  {"max_gross": 1.00, "max_net_long":  0.00, "max_net_short": -0.20},
}

_DEFAULT_REGIME = "Risk-On"
_POSITION_CAP = 0.15  # hard per-position cap: 15% of portfolio (domain rule, regime-independent)

# ── Internal helpers ───────────────────────────────────────────────────────────


def _resolve_caps(regime: str) -> dict[str, float]:
    """
    Return the cap dict for the given regime, falling back to Risk-On if unknown.
    """
    caps = REGIME_CAPS.get(regime)
    if caps is None:
        logger.warning(
            "Unknown regime '%s' — falling back to Risk-On caps.",
            regime,
        )
        caps = REGIME_CAPS[_DEFAULT_REGIME]
    return caps


# ── Public API ─────────────────────────────────────────────────────────────────


def get_current_exposure(
    positions: list[dict],
    portfolio_value: float,
    regime: Optional[str] = None,
) -> "ExposureState":
    """
    Compute current portfolio exposure from a list of open position dicts.

    Each position dict is expected to contain:
        - 'direction'   : str   — 'LONG' or 'SHORT'
        - 'dollar_size' : float — absolute notional value in dollars
        - 'sector'      : str | None — sector label (may be absent or None)

    Args:
        positions      : List of position dicts, typically from the Supabase positions table.
        portfolio_value: Total portfolio equity value in dollars (denominator for all fractions).
        regime         : Current macro regime string. Defaults to 'Risk-On' if None or unknown.

    Returns:
        ExposureState dict with all exposure fractions, regime caps, sector concentration,
        and position count. Fields:
            gross_exposure_pct   — (long_notional + short_notional) / portfolio_value
            net_exposure_pct     — (long_notional - short_notional) / portfolio_value
            max_gross_pct        — regime cap: max gross exposure allowed
            max_net_long_pct     — regime cap: max net-long exposure allowed
            max_net_short_pct    — regime cap: net-short floor (negative value)
            sector_concentration — {sector: pct_of_portfolio} summed across all open positions
            position_count       — total number of open positions
            regime               — macro regime used for cap lookup
    """
    # Avoid division-by-zero; treat a zero portfolio as $1 to surface 0.0 fractions cleanly
    safe_value = portfolio_value if portfolio_value > 0 else 1.0

    regime = regime or _DEFAULT_REGIME
    caps = _resolve_caps(regime)

    long_notional = 0.0
    short_notional = 0.0  # stored as positive; sign applied in net calculation
    sector_buckets: dict[str, float] = defaultdict(float)

    for pos in positions:
        direction = str(pos.get("direction", "LONG")).upper()
        raw_size = float(pos.get("dollar_size") or 0.0)
        sector = pos.get("sector") or "Unknown"

        abs_size = abs(raw_size)

        if direction == "SHORT":
            short_notional += abs_size
        else:
            long_notional += abs_size

        sector_buckets[sector] += abs_size

    gross_notional = long_notional + short_notional
    net_notional = long_notional - short_notional

    gross_exposure_pct = gross_notional / safe_value
    net_exposure_pct = net_notional / safe_value

    sector_concentration: dict[str, float] = {
        sector: round(notional / safe_value, 6)
        for sector, notional in sector_buckets.items()
    }

    # Returned as a plain dict so callers work today even before schemas.py is fully
    # implemented. Once ExposureState is a proper Pydantic model this dict satisfies
    # model_validate() without any caller changes.
    exposure_state: dict = {
        "gross_exposure_pct":   round(gross_exposure_pct, 6),
        "net_exposure_pct":     round(net_exposure_pct, 6),
        "max_gross_pct":        caps["max_gross"],
        "max_net_long_pct":     caps["max_net_long"],
        "max_net_short_pct":    caps["max_net_short"],
        "sector_concentration": sector_concentration,
        "position_count":       len(positions),
        "regime":               regime,
    }

    logger.debug(
        "Exposure snapshot | regime=%s gross=%.2f%% net=%.2f%% positions=%d",
        regime,
        gross_exposure_pct * 100,
        net_exposure_pct * 100,
        len(positions),
    )

    return exposure_state  # type: ignore[return-value]


def check_exposure_breach(
    new_dollar_size: float,
    new_direction: str,
    new_sector: Optional[str],
    current: "ExposureState",
    portfolio_value: float,
) -> tuple[bool, str]:
    """
    Determine whether adding a proposed position would breach any regime-gated limit.

    Checks performed in order (first breach wins):
        1. Hard per-position cap — 15% of portfolio (domain rule, regime-independent)
        2. Gross exposure cap    — projected gross > max_gross for current regime
        3. Net long cap          — projected net > max_net_long (LONG positions only)
        4. Net short floor       — projected net < max_net_short (SHORT positions only)

    Args:
        new_dollar_size : Absolute notional value of the proposed position in dollars.
        new_direction   : 'LONG' or 'SHORT'.
        new_sector      : Sector label for the proposed position (informational only; no
                          per-sector cap is enforced at this layer).
        current         : ExposureState dict as returned by get_current_exposure().
        portfolio_value : Total portfolio equity value in dollars.

    Returns:
        (breached: bool, reason: str)
        reason is an empty string when no breach is detected.
    """
    safe_value = portfolio_value if portfolio_value > 0 else 1.0
    direction_upper = str(new_direction).upper()
    abs_size = abs(new_dollar_size)
    new_pct = abs_size / safe_value

    # Extract current state values — current is typed as ExposureState but backed by dict
    gross_now = float(current["gross_exposure_pct"])  # type: ignore[index]
    net_now = float(current["net_exposure_pct"])       # type: ignore[index]
    max_gross = float(current["max_gross_pct"])        # type: ignore[index]
    max_net_long = float(current["max_net_long_pct"])  # type: ignore[index]
    max_net_short = float(current["max_net_short_pct"])# type: ignore[index]
    regime = str(current.get("regime", _DEFAULT_REGIME))  # type: ignore[union-attr]

    # ── Check 1: Hard per-position cap (15% of portfolio — domain rule) ─────────
    if new_pct > _POSITION_CAP:
        reason = (
            f"Position size {new_pct:.1%} of portfolio exceeds the hard per-position "
            f"cap of {_POSITION_CAP:.0%} (regime: {regime})."
        )
        logger.warning("Exposure breach — %s", reason)
        return True, reason

    # ── Check 2: Gross exposure cap ──────────────────────────────────────────────
    projected_gross = gross_now + new_pct
    if projected_gross > max_gross:
        reason = (
            f"Adding this position would push gross exposure to {projected_gross:.1%}, "
            f"exceeding the {regime} cap of {max_gross:.0%} "
            f"(current gross: {gross_now:.1%})."
        )
        logger.warning("Exposure breach — %s", reason)
        return True, reason

    # ── Check 3: Net long cap (LONG positions only) ──────────────────────────────
    if direction_upper == "LONG":
        projected_net = net_now + new_pct
        if projected_net > max_net_long:
            reason = (
                f"Adding this LONG position would push net exposure to {projected_net:.1%}, "
                f"exceeding the {regime} net-long cap of {max_net_long:.0%} "
                f"(current net: {net_now:.1%})."
            )
            logger.warning("Exposure breach — %s", reason)
            return True, reason

    # ── Check 4: Net short floor (SHORT positions only) ──────────────────────────
    if direction_upper == "SHORT":
        projected_net = net_now - new_pct
        if projected_net < max_net_short:
            reason = (
                f"Adding this SHORT position would push net exposure to {projected_net:.1%}, "
                f"breaching the {regime} net-short floor of {max_net_short:.0%} "
                f"(current net: {net_now:.1%})."
            )
            logger.warning("Exposure breach — %s", reason)
            return True, reason

    logger.debug(
        "No exposure breach | direction=%s size=%.2f%% projected_gross=%.2f%% projected_net=%.2f%%",
        direction_upper,
        new_pct * 100,
        (gross_now + new_pct) * 100,
        (net_now + new_pct if direction_upper == "LONG" else net_now - new_pct) * 100,
    )

    return False, ""
