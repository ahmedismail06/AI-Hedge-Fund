"""
EarningsAlpha runner — orchestrates pre-earnings sizing and drift-hold lifecycle.
Called by research_agent.py Phase 2.6.

Never raises — returns EarningsAlphaOutput with unavailable=True on failure.
"""
from __future__ import annotations

import logging
import os
from datetime import date
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

from backend.earnings_alpha.drift_manager import (
    activate_drift_hold,
    expire_stale_holds,
    get_active_drift_hold,
)
from backend.earnings_alpha.estimate_comparator import (
    compute_signal,
    extrapolate_internal_eps,
)
from backend.earnings_alpha.schemas import (
    DriftHoldState,
    EarningsAlphaOutput,
    PreEarningsSizing,
)

logger = logging.getLogger(__name__)

# Surprise threshold — must exceed this for drift-hold activation
_DRIFT_SURPRISE_THRESHOLD = 0.05

# How many recent quarters the most-recent event date is considered "recent"
# (within this many days we treat reactions[0] as the latest print)
_RECENT_EVENT_DAYS = 45


def _get_client():
    import supabase as _sb
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
    return _sb.create_client(url, key)


def _compute_historical_stats(reactions: list[dict]) -> tuple[Optional[float], Optional[float]]:
    """
    Returns (beat_rate, avg_post_beat_reaction_5d) from last 8Q data.

    beat_rate — fraction of quarters where reported_eps > consensus_eps.
    avg_post_beat_reaction_5d — mean 5-day price return on beat quarters.
    """
    valid = [
        r for r in reactions
        if r.get("reported_eps") is not None and r.get("consensus_eps") is not None
    ]
    if not valid:
        return None, None

    beats = [r for r in valid if r["reported_eps"] > r["consensus_eps"]]
    beat_rate = len(beats) / len(valid)

    reactions_5d = [r["price_reaction_5d"] for r in beats if r.get("price_reaction_5d") is not None]
    avg_5d = sum(reactions_5d) / len(reactions_5d) if reactions_5d else None

    return beat_rate, avg_5d


def _format_summary(
    ticker: str,
    pre: PreEarningsSizing,
    drift: DriftHoldState,
    beat_rate: Optional[float],
    avg_5d: Optional[float],
) -> str:
    spread_str = f"{pre.spread_pct:+.1%}" if pre.spread_pct is not None else "N/A"
    gate_str = "PASSED" if pre.conviction_gate_passed else "FAILED"
    internal_str = f"${pre.internal_eps_estimate:.2f}" if pre.internal_eps_estimate is not None else "N/A"
    consensus_str = f"${pre.consensus_eps:.2f}" if pre.consensus_eps is not None else "N/A"
    beat_str = f"{beat_rate:.0%}" if beat_rate is not None else "N/A"
    react_str = f"{avg_5d:+.1%}" if avg_5d is not None else "N/A"
    drift_str = f"ACTIVE until {drift.hold_until} ({drift.hold_days_remaining}d remaining)" if drift.active else "INACTIVE"

    return (
        f"=== EARNINGS ALPHA ===\n"
        f"Pre-earnings signal: {pre.signal} (spread: {spread_str}, conviction gate: {gate_str})\n"
        f"Internal EPS est: {internal_str} vs consensus: {consensus_str} | Beat rate (8Q): {beat_str}\n"
        f"Avg 5-day return on beats: {react_str}\n"
        f"Drift-hold: {drift_str}"
    )


def _persist(
    ticker: str,
    run_date: str,
    reactions: list[dict],
    pre: PreEarningsSizing,
    drift: DriftHoldState,
) -> None:
    """Upsert the latest run to earnings_events (on ticker, event_date)."""
    if not reactions:
        return
    latest = reactions[0]
    event_date = latest.get("date")
    if not event_date:
        return

    row: dict = {
        "ticker": ticker.upper(),
        "event_date": event_date,
        "reported_eps": latest.get("reported_eps"),
        "consensus_eps": latest.get("consensus_eps"),
        "internal_eps_estimate": pre.internal_eps_estimate,
        "surprise_pct": latest.get("surprise_pct"),
        "price_reaction_1d": latest.get("price_reaction_1d"),
        "price_reaction_5d": latest.get("price_reaction_5d"),
        "drift_hold_active": drift.active,
        "drift_hold_until": drift.hold_until,
        "pre_earnings_signal": pre.signal,
        "run_date": run_date,
    }
    try:
        _get_client().table("earnings_events").upsert(row, on_conflict="ticker,event_date").execute()
        logger.debug("_persist(%s): upserted earnings_events row for %s", ticker, event_date)
    except Exception as exc:
        logger.warning("_persist(%s): Supabase upsert failed — %s", ticker, exc)


def run_earnings_alpha(
    ticker: str,
    reactions: list[dict],
    fmp_data: dict,
    conviction_score: float,
) -> EarningsAlphaOutput:
    """
    Orchestrate pre-earnings sizing and drift-hold lifecycle for a ticker.

    Args:
        ticker: Stock ticker symbol.
        reactions: Output of get_earnings_reactions() — list of dicts, newest first.
        fmp_data: Output of get_fmp_data() — provides consensus_eps_current_year.
        conviction_score: InvestmentMemo conviction_score (0-10); gates SIZE_UP signal.

    Returns:
        EarningsAlphaOutput with pre_earnings signal, drift_hold state, and summary.
    """
    run_date = str(date.today())

    try:
        # Step 1: Expire any stale drift-hold windows
        expire_stale_holds()

        # Step 2: Internal EPS extrapolation
        internal_est = extrapolate_internal_eps(reactions)

        # Step 3: Consensus EPS — prefer most-recent quarter's actual consensus;
        #         fall back to FMP annual / 4 quarters as quarterly proxy
        consensus_eps: Optional[float] = None
        if reactions and reactions[0].get("consensus_eps") is not None:
            consensus_eps = reactions[0]["consensus_eps"]
        elif fmp_data.get("consensus_eps_current_year"):
            consensus_eps = fmp_data["consensus_eps_current_year"] / 4.0

        # Step 4: Pre-earnings signal
        pre = compute_signal(internal_est, consensus_eps, conviction_score)

        # Step 5: Detect fresh earnings print (event_date within _RECENT_EVENT_DAYS)
        drift = get_active_drift_hold(ticker)
        if not drift.active and reactions:
            latest = reactions[0]
            event_date_str = latest.get("date")
            surprise = latest.get("surprise_pct")
            if event_date_str and surprise is not None:
                event_dt = date.fromisoformat(event_date_str)
                days_since = (date.today() - event_dt).days
                if 0 <= days_since <= _RECENT_EVENT_DAYS and surprise > _DRIFT_SURPRISE_THRESHOLD:
                    hold_until = activate_drift_hold(ticker, surprise, event_date_str)
                    if hold_until:
                        from datetime import timedelta
                        drift = DriftHoldState(
                            active=True,
                            surprise_pct=surprise,
                            hold_until=hold_until,
                            hold_days_remaining=(
                                date.fromisoformat(hold_until) - date.today()
                            ).days,
                        )

        # Step 6: Historical stats
        beat_rate, avg_5d = _compute_historical_stats(reactions)

        # Step 7: Build summary
        summary = _format_summary(ticker, pre, drift, beat_rate, avg_5d)

        # Step 8: Persist
        _persist(ticker, run_date, reactions, pre, drift)

        return EarningsAlphaOutput(
            ticker=ticker.upper(),
            run_date=run_date,
            pre_earnings=pre,
            drift_hold=drift,
            historical_beat_rate=beat_rate,
            avg_post_beat_reaction_5d=avg_5d,
            summary=summary,
        )

    except Exception as exc:
        logger.error("run_earnings_alpha(%s): unexpected error — %s", ticker, exc)
        fallback_pre = PreEarningsSizing(
            signal="HOLD",
            conviction_gate_passed=False,
            rationale=f"EarningsAlpha unavailable: {exc}",
        )
        fallback_drift = DriftHoldState(active=False)
        return EarningsAlphaOutput(
            ticker=ticker.upper(),
            run_date=run_date,
            pre_earnings=fallback_pre,
            drift_hold=fallback_drift,
            summary="=== EARNINGS ALPHA ===\nUnavailable — see logs.",
            unavailable=True,
            unavailable_reason=str(exc),
        )
