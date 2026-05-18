"""
Short screening agent.

Runs after the long screener (4 PM ET) and sources short candidates from:
  1. Today's watchlist rows (0 extra API calls — data already fetched)
  2. Supplementary wide universe $2B–$10B (weekly-cached)

Writes top 2-5 short candidates to the short_candidates table with
queued_for_research=True. The research scheduler picks them up at 5 PM ET.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta, timezone
from datetime import datetime as _dt

from dotenv import load_dotenv

load_dotenv()

from backend.memory.vector_store import _get_client
from backend.notifications.events import notify_event
from backend.screener.short_scorer import compute_short_scores, select_short_candidates
from backend.screener.short_universe import build_wide_universe

logger = logging.getLogger(__name__)


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


def _store_short_candidates(
    results: list,
    run_date: date,
    regime: str,
) -> None:
    client = _get_client()
    rows = []
    for r in results:
        rows.append({
            "run_date":            run_date.isoformat(),
            "ticker":              r.ticker,
            "short_score":         float(r.short_score),
            "deterioration_score": float(r.deterioration_score),
            "overvaluation_score": float(r.overvaluation_score),
            "accruals_score":      float(r.accruals_score),
            "cash_burn_score":     float(r.cash_burn_score),
            "source":              r.source,
            "market_cap_m":        float(r.market_cap_m) if r.market_cap_m is not None else None,
            "sector":              r.sector,
            "adv_k":               float(r.adv_k) if r.adv_k is not None else None,
            "beneish_m_score":     float(r.beneish_m_score) if r.beneish_m_score is not None else None,
            "beneish_flag":        r.beneish_flag,
            "raw_factors":         r.raw_factors,
            "queued_for_research": True,
            "regime":              regime,
        })

    if not rows:
        return

    try:
        client.table("short_candidates").upsert(
            rows, on_conflict="run_date,ticker"
        ).execute()
        logger.info("short_candidates: upserted %d rows for %s", len(rows), run_date)
    except Exception as exc:
        logger.error("short_candidates upsert failed: %s", exc)


def run_short_screening(regime: str | None = None) -> list[dict]:
    """
    Run the short screening pipeline.

    Returns a list of dicts for selected short candidates.
    Never raises — logs errors and returns [] on failure.
    """
    try:
        today = date.today()
        regime = regime or _read_regime()
        logger.info("=== Short screening starting | date=%s regime=%s ===", today, regime)

        # ── Step 1: Read today's watchlist ────────────────────────────────────
        client = _get_client()
        try:
            wl_result = (
                client.table("watchlist")
                .select("ticker,market_cap_m,sector,adv_k,beneish_m_score,beneish_flag,raw_factors")
                .eq("run_date", today.isoformat())
                .not_.is_("raw_factors", "null")
                .execute()
            )
            watchlist_rows = [
                {
                    "ticker":        r["ticker"],
                    "market_cap_m":  r.get("market_cap_m"),
                    "sector":        r.get("sector"),
                    "adv_k":         r.get("adv_k"),
                    "beneish_m_score": r.get("beneish_m_score"),
                    "beneish_flag":  r.get("beneish_flag"),
                    "raw_factors":   r.get("raw_factors") or {},
                    "source":        "watchlist",
                }
                for r in (wl_result.data or [])
            ]
            logger.info("Short screening: %d watchlist rows loaded", len(watchlist_rows))
        except Exception as exc:
            logger.error("Short screening: watchlist read failed — %s", exc)
            watchlist_rows = []

        if not watchlist_rows:
            logger.warning("Short screening: no watchlist rows for %s — aborting", today)
            return []

        # ── Step 2: Supplement with wide universe ($2B–$10B) ─────────────────
        try:
            wide_rows = build_wide_universe(use_cache=True)
            logger.info("Short screening: %d wide universe candidates", len(wide_rows))
        except Exception as exc:
            logger.warning("Short screening: wide universe failed — %s", exc)
            wide_rows = []

        # Deduplicate: prefer watchlist rows (more data) at $2B boundary
        watchlist_tickers = {r["ticker"] for r in watchlist_rows}
        wide_rows = [r for r in wide_rows if r["ticker"] not in watchlist_tickers]

        all_candidates = watchlist_rows + wide_rows
        logger.info("Short screening: %d total candidates", len(all_candidates))

        # ── Step 3: Score ─────────────────────────────────────────────────────
        scored = compute_short_scores(all_candidates, regime=regime)

        # ── Step 4: Select top 2-5 ────────────────────────────────────────────
        selected = select_short_candidates(scored, max_candidates=5)
        logger.info("Short screening: %d candidates selected (score >= 6.0)", len(selected))

        if not selected:
            logger.info("Short screening: no candidates met threshold — done")
            return []

        # ── Step 5: Freshness check ───────────────────────────────────────────
        fresh_candidates = []
        for r in selected:
            if _is_recently_researched(r.ticker, days=14):
                logger.info("Short screening: skipping %s — SHORT memo < 14 days old", r.ticker)
            else:
                fresh_candidates.append(r)

        if not fresh_candidates:
            logger.info("Short screening: all candidates have recent memos — done")
            return []

        # ── Step 6: Write to short_candidates ────────────────────────────────
        _store_short_candidates(fresh_candidates, today, regime)

        # ── Step 7: Notify ────────────────────────────────────────────────────
        summary = [
            {"ticker": r.ticker, "short_score": r.short_score, "sector": r.sector}
            for r in fresh_candidates
        ]
        notify_event("SHORT_CANDIDATES_SELECTED", {
            "count": len(fresh_candidates),
            "candidates": summary,
            "regime": regime,
        })
        logger.info(
            "=== Short screening complete | %d candidates: %s ===",
            len(fresh_candidates),
            [r.ticker for r in fresh_candidates],
        )
        return [
            {"ticker": r.ticker, "short_score": r.short_score, "sector": r.sector}
            for r in fresh_candidates
        ]

    except Exception as exc:
        logger.exception("run_short_screening: unexpected failure — %s", exc)
        return []
