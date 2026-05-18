"""
Short screening agent.

Runs after the long screener (4 PM ET). New v2 flow:

  1. Read today's long-watchlist tickers (used to exclude overlap)
  2. Build dedicated short universe ($2B–$20B, well-covered, low SI)
  3. Run the trigger-count scorer (7 binary bear triggers; ≥3 to qualify)
  4. Apply 14-day SHORT-memo freshness gate
  5. Write qualifying rows to short_candidates with source=TRIGGER_SCORER,
     queued_for_research=True. The research scheduler picks them up.

This agent does NOT handle the event-driven queue path (BENEISH_EXCLUDED,
NEWS_GUIDANCE_CUT, SEC_RESTATEMENT, MANUAL). Those are written directly to
short_candidates by their respective producers — e.g. screening_agent
auto-enqueues Beneish-EXCLUDED rows. The research scheduler reads everything
out of short_candidates regardless of source.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta, timezone
from datetime import datetime as _dt
from typing import Iterable

from dotenv import load_dotenv

load_dotenv()

from backend.memory.vector_store import _get_client
from backend.notifications.events import notify_event
from backend.screener.short_trigger_scorer import (
    TriggerResult,
    score_triggers,
    select_qualifying,
)
from backend.screener.short_universe import build_short_universe

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _read_regime() -> str:
    try:
        client = _get_client()
        result = (
            client.table("macro_briefings")
            .select("regime")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            return result.data[0].get("regime", "Transitional")
    except Exception as exc:
        logger.warning("_read_regime failed, defaulting to Transitional: %s", exc)
    return "Transitional"


def _read_long_watchlist_tickers(run_date: date) -> set[str]:
    """Today's long-watchlist tickers — excluded from the short universe to avoid overlap."""
    try:
        client = _get_client()
        result = (
            client.table("watchlist")
            .select("ticker")
            .eq("run_date", run_date.isoformat())
            .execute()
        )
        return {r["ticker"] for r in (result.data or []) if r.get("ticker")}
    except Exception as exc:
        logger.warning("Long watchlist read failed (continuing without exclude set): %s", exc)
        return set()


def _read_beneish_lookup(run_date: date, tickers: Iterable[str]) -> dict[str, dict]:
    """Pull Beneish data from watchlist.raw_factors for tickers we care about.

    Used as input to T4 of the trigger scorer. Most short-universe tickers won't
    be in the long watchlist (cap bands differ), so this lookup is usually small
    or empty — T4 just evaluates as None and skips.
    """
    tickers = list(tickers)
    if not tickers:
        return {}
    try:
        client = _get_client()
        result = (
            client.table("watchlist")
            .select("ticker,beneish_m_score,beneish_flag")
            .eq("run_date", run_date.isoformat())
            .in_("ticker", tickers)
            .execute()
        )
        return {
            r["ticker"]: {"m_score": r.get("beneish_m_score"), "gate_result": r.get("beneish_flag")}
            for r in (result.data or [])
        }
    except Exception as exc:
        logger.warning("Beneish lookup failed: %s", exc)
        return {}


def _is_recently_researched(ticker: str, days: int = 14) -> bool:
    """Return True if a SHORT memo for this ticker exists within the past `days` days."""
    try:
        client = _get_client()
        cutoff = (_dt.now(timezone.utc) - timedelta(days=days)).isoformat()
        result = (
            client.table("memos")
            .select("id")
            .eq("ticker", ticker)
            .eq("verdict", "SHORT")
            .gte("created_at", cutoff)
            .limit(1)
            .execute()
        )
        return bool(result.data)
    except Exception as exc:
        logger.warning("_is_recently_researched check failed for %s: %s", ticker, exc)
        return False


def _store_trigger_candidates(
    results: list[TriggerResult], run_date: date, regime: str,
) -> int:
    """Upsert qualifying trigger-scorer rows into short_candidates."""
    if not results:
        return 0
    try:
        client = _get_client()
    except Exception as exc:
        logger.error("short_candidates: client unavailable — %s", exc)
        return 0

    rows = [
        {
            "run_date":            run_date.isoformat(),
            "ticker":              r.ticker,
            "source":              "TRIGGER_SCORER",
            "priority":            5,
            "evidence":            {
                "triggers_fired":     r.triggers_fired,
                "triggers_evaluated": r.triggers_evaluated,
                "trigger_count":      r.trigger_count,
                "raw":                r.evidence,
                "short_interest_pct": r.short_interest_pct,
            },
            "trigger_count":       r.trigger_count,
            "triggers_fired":      r.triggers_fired,
            "market_cap_m":        r.market_cap_m,
            "sector":              r.sector,
            "adv_k":               r.adv_k,
            "beneish_m_score":     r.beneish_m_score,
            "beneish_flag":        r.beneish_flag,
            "queued_for_research": True,
            "regime":              regime,
        }
        for r in results
    ]

    try:
        client.table("short_candidates").upsert(rows, on_conflict="run_date,ticker").execute()
        logger.info("short_candidates: upserted %d TRIGGER_SCORER rows", len(rows))
        return len(rows)
    except Exception as exc:
        logger.error("short_candidates upsert failed: %s", exc)
        return 0


# ── Main entrypoint ───────────────────────────────────────────────────────────
def run_short_screening(regime: str | None = None) -> list[dict]:
    """
    Run the dedicated short-screening pipeline.

    Returns a list of dicts for qualifying tickers. Never raises — logs errors
    and returns [] on failure.
    """
    try:
        today = date.today()
        regime = regime or _read_regime()
        logger.info("=== Short screening v2 starting | date=%s regime=%s ===", today, regime)

        # ── Step 1: exclude set = today's long watchlist (no overlap) ─────────
        exclude = _read_long_watchlist_tickers(today)
        logger.info("Short screening: excluding %d tickers from long watchlist", len(exclude))

        # ── Step 2: build dedicated short universe (cached, 7-day TTL) ────────
        universe = build_short_universe(use_cache=True, exclude_tickers=exclude)
        if not universe:
            logger.warning("Short screening: empty universe (cache miss spawned background rebuild) — done")
            return []
        logger.info("Short screening: %d tickers in universe", len(universe))

        # ── Step 3: Beneish lookup (best-effort — most won't be in long wl) ───
        beneish_lookup = _read_beneish_lookup(today, [r["ticker"] for r in universe])

        # ── Step 4: score triggers ────────────────────────────────────────────
        scored = score_triggers(universe, beneish_lookup=beneish_lookup)
        qualifying = select_qualifying(scored)
        logger.info("Short screening: %d tickers qualify (≥3 triggers, ≥4 evaluable)", len(qualifying))
        if not qualifying:
            return []

        # ── Step 5: freshness gate ────────────────────────────────────────────
        fresh = [r for r in qualifying if not _is_recently_researched(r.ticker, days=14)]
        skipped = [r.ticker for r in qualifying if r not in fresh]
        if skipped:
            logger.info("Short screening: %d skipped (SHORT memo < 14 days): %s",
                        len(skipped), skipped)
        if not fresh:
            return []

        # ── Step 6: enqueue to short_candidates ───────────────────────────────
        n_stored = _store_trigger_candidates(fresh, today, regime)

        # ── Step 7: notify ────────────────────────────────────────────────────
        summary = [
            {
                "ticker":         r.ticker,
                "trigger_count":  r.trigger_count,
                "triggers":       r.triggers_fired,
                "sector":         r.sector,
                "market_cap_m":   r.market_cap_m,
            }
            for r in fresh
        ]
        notify_event("SHORT_CANDIDATES_SELECTED", {
            "count":      len(fresh),
            "candidates": summary,
            "regime":     regime,
            "source":     "TRIGGER_SCORER",
        })
        logger.info(
            "=== Short screening v2 complete | %d enqueued (%d stored): %s ===",
            len(fresh), n_stored, [r.ticker for r in fresh],
        )
        return summary

    except Exception as exc:
        logger.exception("run_short_screening: unexpected failure — %s", exc)
        return []
