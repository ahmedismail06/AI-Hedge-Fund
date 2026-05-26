"""
Stop Loss Engine — 3-tier stop structure.

Tier 1 — Position stop: per-trade max loss (-8% normal, -5% Risk-Off/Stagflation)
Tier 2 — Strategy/sector stop: aggregate sector drawdown (-15% normal, -10% Risk-Off/Stagflation)
Tier 3 — Portfolio stop: total portfolio drawdown (-20% normal, -15% Risk-Off/Stagflation)

Thresholds automatically tighten in Risk-Off and Stagflation regimes (per domain rules).
"""

import logging
from collections import defaultdict
from datetime import date
from typing import Optional

from backend.risk.schemas import StopEvent

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Stop thresholds by regime tier
# Values are negative fractions (e.g. -0.08 = -8%)
# ──────────────────────────────────────────────────────────────────────────────

_TIGHT_REGIMES = {"Risk-Off", "Stagflation"}

_TIER1_NORMAL = -0.08   # -8% position stop (normal regimes)
_TIER1_TIGHT  = -0.05   # -5% position stop (Risk-Off / Stagflation)

# WARN when current_price is within this fraction *above* stop_loss_price.
# At 10%: PAR stop=$12.88, price=$13.79 → (13.79-12.88)/12.88 = 7.1% → fires WARN.
_APPROACHING_PCT = 0.10

_TIER2_NORMAL = -0.15   # -15% sector stop (normal)
_TIER2_TIGHT  = -0.10   # -10% sector stop (tight)

_TIER3_NORMAL = -0.20   # -20% portfolio stop (normal)
_TIER3_TIGHT  = -0.15   # -15% portfolio stop (tight)


def _tier1_threshold(regime: str, drift_hold_active: bool = False) -> float:
    # Drift-hold suppresses Risk-Off/Stagflation tightening for 45 days post
    # positive earnings surprise, allowing post-print momentum to run.
    if drift_hold_active:
        return _TIER1_NORMAL
    return _TIER1_TIGHT if regime in _TIGHT_REGIMES else _TIER1_NORMAL


def _drift_hold_active(pos: dict) -> bool:
    """Return True if this position has an active post-earnings drift-hold window."""
    hold_until = pos.get("drift_hold_until")
    if not hold_until:
        return False
    try:
        return date.fromisoformat(str(hold_until)[:10]) >= date.today()
    except (ValueError, TypeError):
        return False


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

    t2_thresh = _tier2_threshold(regime)
    t3_thresh = _tier3_threshold(regime)

    # ── Tier 1: per-position stop (breached) + approaching-stop WARN ─────────
    for pos in positions:
        ticker = pos.get("ticker", "?")
        direction = str(pos.get("direction", "LONG")).upper()
        pnl_pct = _safe_float(pos.get("pnl_pct"), 0.0)
        current_price = _safe_float(pos.get("current_price"))
        stop_price = _safe_float(pos.get("stop_loss_price"))
        entry_price = _safe_float(pos.get("entry_price"))
        sector = pos.get("sector")

        # Per-position threshold — drift-hold suppresses Risk-Off tightening
        drift_held = _drift_hold_active(pos)
        t1_thresh = _tier1_threshold(regime, drift_hold_active=drift_held)

        logger.debug(
            "stop_check %s (%s): pnl_pct=%.2f%% current=$%.4f entry=$%.4f stop=$%.4f "
            "tier1_thresh=%.1f%% drift_hold=%s",
            ticker,
            direction,
            (pnl_pct or 0) * 100,
            current_price or 0,
            entry_price or 0,
            stop_price or 0,
            t1_thresh * 100,
            drift_held,
        )

        if pnl_pct <= t1_thresh:
            logger.warning(
                "STOP BREACHED — %s (%s) pnl_pct=%.2f%% <= threshold=%.1f%% (regime=%s)",
                ticker, direction, pnl_pct * 100, t1_thresh * 100, regime,
            )
            events.append(StopEvent(
                ticker=ticker,
                tier=1,
                entry_price=entry_price,
                current_price=current_price,
                stop_price=stop_price,
                pct_move=pnl_pct,
                regime=regime,
                sector=sector,
                approaching=False,
                direction=direction,
            ))
        elif stop_price and current_price:
            # Direction-aware approaching stop check.
            # LONG: stop is a floor — warn when price is within 10% above the stop.
            # SHORT: stop is a ceiling — warn when price is within 10% below the stop.
            if direction == "SHORT" and current_price < stop_price:
                distance_pct = (stop_price - current_price) / stop_price
                if distance_pct <= _APPROACHING_PCT:
                    logger.warning(
                        "APPROACHING STOP (SHORT) — %s current=$%.4f stop=$%.4f distance=%.1f%%",
                        ticker, current_price, stop_price, distance_pct * 100,
                    )
                    events.append(StopEvent(
                        ticker=ticker,
                        tier=1,
                        entry_price=entry_price,
                        current_price=current_price,
                        stop_price=stop_price,
                        pct_move=pnl_pct,
                        regime=regime,
                        sector=sector,
                        approaching=True,
                        direction=direction,
                    ))
            elif direction != "SHORT" and current_price > stop_price:
                distance_pct = (current_price - stop_price) / stop_price
                if distance_pct <= _APPROACHING_PCT:
                    logger.warning(
                        "APPROACHING STOP — %s current=$%.4f stop=$%.4f distance=%.1f%% (WARN threshold=%.0f%%)",
                        ticker, current_price, stop_price, distance_pct * 100, _APPROACHING_PCT * 100,
                    )
                    events.append(StopEvent(
                        ticker=ticker,
                        tier=1,
                        entry_price=entry_price,
                        current_price=current_price,
                        stop_price=stop_price,
                        pct_move=pnl_pct,
                        regime=regime,
                        sector=sector,
                        approaching=True,
                        direction=direction,
                    ))

    # ── Tier 2/3: per-position price stops (BREACH / CRITICAL) ──────────────
    # stop_tier2 and stop_tier3 are absolute price levels stored per position.
    # A position that has blown through these levels fires BREACH/CRITICAL
    # immediately, regardless of portfolio/sector aggregate state.
    # This prevents a single position loss being diluted by a healthy portfolio.
    for pos in positions:
        ticker = pos.get("ticker", "?")
        direction = str(pos.get("direction", "LONG")).upper()
        current_price = _safe_float(pos.get("current_price"))
        entry_price = _safe_float(pos.get("entry_price"))
        pnl_pct = _safe_float(pos.get("pnl_pct"), 0.0)
        sector = pos.get("sector")
        stop_t2 = _safe_float(pos.get("stop_tier2"))
        stop_t3 = _safe_float(pos.get("stop_tier3"))

        if current_price is None:
            continue

        # For SHORT: stops are ceilings (price rising is bad)
        # For LONG:  stops are floors  (price falling is bad)
        if direction == "SHORT":
            t2_breached = stop_t2 is not None and current_price >= stop_t2
            t3_breached = stop_t3 is not None and current_price >= stop_t3
        else:
            t2_breached = stop_t2 is not None and current_price <= stop_t2
            t3_breached = stop_t3 is not None and current_price <= stop_t3

        if t3_breached:
            logger.critical(
                "TIER 3 PRICE STOP BREACHED — %s (%s) current=$%.4f >= stop_tier3=$%.4f (pnl=%.1f%%)",
                ticker, direction, current_price, stop_t3, (pnl_pct or 0) * 100,
            )
            events.append(StopEvent(
                ticker=ticker,
                tier=3,
                entry_price=entry_price,
                current_price=current_price,
                stop_price=stop_t3,
                pct_move=pnl_pct,
                regime=regime,
                sector=sector,
                approaching=False,
                direction=direction,
            ))
        elif t2_breached:
            logger.warning(
                "TIER 2 PRICE STOP BREACHED — %s (%s) current=$%.4f >= stop_tier2=$%.4f (pnl=%.1f%%)",
                ticker, direction, current_price, stop_t2, (pnl_pct or 0) * 100,
            )
            events.append(StopEvent(
                ticker=ticker,
                tier=2,
                entry_price=entry_price,
                current_price=current_price,
                stop_price=stop_t2,
                pct_move=pnl_pct,
                regime=regime,
                sector=sector,
                approaching=False,
                direction=direction,
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
