"""
Stop Loss Engine — 3-tier stop structure.

Tier 1 — Position stop: per-trade max loss (-8% normal, -5% Risk-Off/Stagflation)
Tier 2 — Strategy/sector stop: aggregate sector drawdown (-15% normal, -10% Risk-Off/Stagflation)
Tier 3 — Portfolio stop: total portfolio drawdown (-20% normal, -15% Risk-Off/Stagflation)

Thresholds automatically tighten in Risk-Off and Stagflation regimes (per domain rules).
"""

from collections import defaultdict
from typing import Optional

from backend.risk.schemas import StopEvent

# ──────────────────────────────────────────────────────────────────────────────
# Stop thresholds by regime tier
# Values are negative fractions (e.g. -0.08 = -8%)
# ──────────────────────────────────────────────────────────────────────────────

_TIGHT_REGIMES = {"Risk-Off", "Stagflation"}

_TIER1_NORMAL = -0.08   # -8% position stop (normal regimes)
_TIER1_TIGHT  = -0.05   # -5% position stop (Risk-Off / Stagflation)

_TIER2_NORMAL = -0.15   # -15% sector stop (normal)
_TIER2_TIGHT  = -0.10   # -10% sector stop (tight)

_TIER3_NORMAL = -0.20   # -20% portfolio stop (normal)
_TIER3_TIGHT  = -0.15   # -15% portfolio stop (tight)


def _tier1_threshold(regime: str) -> float:
    return _TIER1_TIGHT if regime in _TIGHT_REGIMES else _TIER1_NORMAL


def _tier2_threshold(regime: str) -> float:
    return _TIER2_TIGHT if regime in _TIGHT_REGIMES else _TIER2_NORMAL


def _tier3_threshold(regime: str) -> float:
    return _TIER3_TIGHT if regime in _TIGHT_REGIMES else _TIER3_NORMAL


def check_stops(positions: list[dict], regime: str) -> list[StopEvent]:
    """
    Check all 3 tiers of stops for the current position set.

    Args:
        positions: list of position dicts from the `positions` Supabase table.
                   Each dict must have: ticker, pnl_pct, entry_price,
                   current_price, stop_loss_price (optional), sector (optional),
                   pct_of_portfolio, direction.
        regime:    current macro regime string.

    Returns:
        List of StopEvent objects for every breached tier. Empty list = all clear.
    """
    events: list[StopEvent] = []

    t1_thresh = _tier1_threshold(regime)
    t2_thresh = _tier2_threshold(regime)
    t3_thresh = _tier3_threshold(regime)

    # ── Tier 1: per-position stop ─────────────────────────────────────────────
    for pos in positions:
        pnl_pct = _safe_float(pos.get("pnl_pct"), 0.0)
        if pnl_pct <= t1_thresh:
            events.append(StopEvent(
                ticker=pos.get("ticker"),
                tier=1,
                entry_price=_safe_float(pos.get("entry_price")),
                current_price=_safe_float(pos.get("current_price")),
                stop_price=_safe_float(pos.get("stop_loss_price")),
                pct_move=pnl_pct,
                regime=regime,
                sector=pos.get("sector"),
            ))

    # ── Tier 2: sector aggregate stop ────────────────────────────────────────
    # Aggregate weighted pnl_pct by sector (weight = pct_of_portfolio)
    sector_pnl: dict[str, list[float]] = defaultdict(list)
    sector_weight: dict[str, float] = defaultdict(float)

    for pos in positions:
        sector = pos.get("sector") or "Unknown"
        pnl_pct = _safe_float(pos.get("pnl_pct"), 0.0)
        weight = _safe_float(pos.get("pct_of_portfolio"), 0.0)
        sector_pnl[sector].append(pnl_pct)
        sector_weight[sector] += weight

    for sector, pnl_list in sector_pnl.items():
        total_weight = sector_weight[sector]
        if total_weight <= 0:
            continue
        # Weight-average pnl_pct within sector
        sector_avg_pnl = sum(pnl_list) / len(pnl_list)
        if sector_avg_pnl <= t2_thresh:
            events.append(StopEvent(
                ticker=None,
                tier=2,
                pct_move=sector_avg_pnl,
                regime=regime,
                sector=sector,
            ))

    # ── Tier 3: portfolio stop ────────────────────────────────────────────────
    if positions:
        total_weight = sum(_safe_float(p.get("pct_of_portfolio"), 0.0) for p in positions)
        if total_weight > 0:
            portfolio_pnl = sum(
                _safe_float(p.get("pnl_pct"), 0.0) * _safe_float(p.get("pct_of_portfolio"), 0.0)
                for p in positions
            ) / total_weight
            if portfolio_pnl <= t3_thresh:
                events.append(StopEvent(
                    ticker=None,
                    tier=3,
                    pct_move=portfolio_pnl,
                    regime=regime,
                ))

    return events


def _safe_float(value, default: Optional[float] = None) -> Optional[float]:
    """Safely coerce Decimal/None values from Supabase to float."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
