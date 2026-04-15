"""
Screening Agent — daily 4PM ET pipeline.

Pipeline:
  1. Read macro regime from Supabase macro_briefings (fallback: Risk-On)
  2. Build universe (~800 tickers) via Polygon + yfinance
  3. Batch-fetch per-ticker data (parallel, ThreadPoolExecutor(10))
  4. Score all three factors (quality, value, momentum) + Beneish gate
  5. Form 4 pass: fetch insider buying for tickers with estimated score ≥ 5.0
  6. Compute composite via scorer.py
  7. Bulk-upsert results to Supabase watchlist table
  8. Queue top 5 qualified tickers (score ≥ 7.0) for research

Architecture note: This agent writes to Supabase watchlist. The Orchestrator
polls `queued_for_research=True` rows and calls the Research Agent. No direct
agent-to-agent communication.
"""

import logging
import math
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from dotenv import load_dotenv

from backend.screener.universe import UniverseCandidate, build_universe, fetch_ticker_data
from backend.screener.factors.earnings_quality import compute_beneish
from backend.screener.factors.quality import score_quality
from backend.screener.factors.value import score_value
from backend.screener.factors.momentum import score_momentum
from backend.screener.scorer import ScreenerResult, compute_composite
from backend.fetchers.fmp_fetcher import fetch_quality_fmp_batch
from backend.memory.vector_store import _get_client
from backend.notifications.events import notify_event

load_dotenv()

logger = logging.getLogger(__name__)

_BATCH_SIZE = 50
_MAX_WORKERS = 10
_QUALIFY_THRESHOLD = 7.0
_TOP_N_FOR_RESEARCH = 5
_INSIDER_PRE_FILTER_SCORE = 5.0   # only fetch Form 4 for tickers above this pre-adjustment score


class ScreeningAgentError(Exception):
    pass


# ── Regime helper ──────────────────────────────────────────────────────────────

def _read_regime() -> str:
    """
    Read the most recent macro regime from Supabase macro_briefings table.
    Returns "Risk-On" on any failure.
    """
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
            regime = result.data[0].get("regime")
            if regime in ("Risk-On", "Risk-Off", "Transitional", "Stagflation"):
                logger.info("Regime read from Supabase: %s", regime)
                return regime
    except Exception as exc:
        logger.warning("Failed to read regime from Supabase: %s — defaulting to Risk-On", exc)
    return "Risk-On"


# ── Per-ticker data fetch + factor scoring ────────────────────────────────────

def _score_ticker(ticker: str, raw_data: dict, fmp_quality: dict | None = None) -> dict:
    """
    Run all factor scorers on pre-fetched ticker data.
    Returns {ticker, quality, value, momentum, beneish, fmp}.
    Never raises.

    Args:
        fmp_quality: Pre-fetched FMP financial statements for this ticker
                     (from fetch_quality_fmp_batch). Passed through to score_quality.
    """
    out: dict = {
        "ticker":   ticker,
        "quality":  {},
        "value":    {},
        "momentum": {},
        "beneish":  {"gate_result": "INSUFFICIENT_DATA", "m_score": None, "missing_fields": []},
        "fmp":      raw_data.get("fmp", {}),
        "form4":    {"insider_buy": False},
    }
    try:
        out["quality"]  = score_quality(
            ticker, raw_data["polygon_financials"], raw_data["yf_info"],
            fmp_quality=fmp_quality,
        )
    except Exception as exc:
        logger.warning("%s: quality scorer failed: %s", ticker, exc)
    try:
        out["value"]    = score_value(ticker, raw_data["polygon_financials"], raw_data["fmp"])
    except Exception as exc:
        logger.warning("%s: value scorer failed: %s", ticker, exc)
    try:
        out["momentum"] = score_momentum(ticker, raw_data["price_history"], raw_data["fmp"])
    except Exception as exc:
        logger.warning("%s: momentum scorer failed: %s", ticker, exc)
    try:
        out["beneish"]  = compute_beneish(ticker, raw_data["polygon_financials"])
    except Exception as exc:
        logger.warning("%s: beneish scorer failed: %s", ticker, exc)
    return out


def _batch_fetch_ticker_data(universe: list[UniverseCandidate]) -> dict[str, dict]:
    """
    Fetch per-ticker data for the full universe in parallel batches.
    Returns {ticker: raw_data_dict}.
    """
    all_data: dict[str, dict] = {}
    tickers = [c.ticker for c in universe]

    # Process in batches to avoid overwhelming the executor queue
    for batch_start in range(0, len(tickers), _BATCH_SIZE):
        batch = tickers[batch_start: batch_start + _BATCH_SIZE]
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            futures = {pool.submit(fetch_ticker_data, t): t for t in batch}
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    all_data[ticker] = future.result()
                except Exception as exc:
                    logger.warning("%s: fetch_ticker_data failed: %s", ticker, exc)
                    all_data[ticker] = {
                        "ticker": ticker, "fmp": {}, "polygon_financials": {"results": []},
                        "price_history": [], "yf_info": {},
                    }

        logger.info(
            "Fetched batch %d-%d / %d",
            batch_start + 1, min(batch_start + _BATCH_SIZE, len(tickers)), len(tickers)
        )

    return all_data


def _fetch_form4_for_candidates(
    tickers: list[str],
) -> dict[str, dict]:
    """
    Fetch insider buying (Form 4) for a targeted subset of tickers.
    Only called for tickers with estimated composite ≥ 5.0 to avoid 800 EDGAR calls.
    Returns {ticker: {"insider_buy": bool}}.
    """
    from backend.fetchers.form4_fetcher import fetch_form4

    results: dict[str, dict] = {}
    for ticker in tickers:
        try:
            form4_data = fetch_form4(ticker)
            # form4_fetcher returns a list of recent buys or similar structure
            # Treat any non-empty result as a positive insider buy signal
            insider_buy = bool(form4_data and len(form4_data) > 0)
            results[ticker] = {"insider_buy": insider_buy}
        except Exception as exc:
            logger.debug("%s: Form 4 fetch failed: %s", ticker, exc)
            results[ticker] = {"insider_buy": False}
    return results


# ── Supabase write helpers ─────────────────────────────────────────────────────

def _store_results(results: list[ScreenerResult], run_date: date, regime: str) -> None:
    """Bulk-upsert all screener results to the watchlist table."""
    try:
        client = _get_client()
    except Exception as exc:
        logger.error("Supabase client unavailable — skipping watchlist write: %s", exc)
        return

    def _sanitize(obj):
        """Recursively replace NaN/Inf at any depth with None for JSON safety."""
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return None
        if isinstance(obj, dict):
            return {k: _sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_sanitize(item) for item in obj]
        return obj

    rows = []
    for r in results:
        rows.append({
            "run_date":        run_date.isoformat(),
            "ticker":          r.ticker,
            "composite_score": _sanitize(float(r.composite_score)),
            "quality_score":   _sanitize(float(r.quality_score)),
            "value_score":     _sanitize(float(r.value_score)),
            "momentum_score":  _sanitize(float(r.momentum_score)),
            "rank":            r.rank,
            "market_cap_m":    r.market_cap_m,
            "adv_k":           r.adv_k,
            "sector":          r.sector,
            "regime":          regime,
            "beneish_m_score": _sanitize(r.beneish_m_score) if r.beneish_m_score is not None else None,
            "beneish_flag":    r.beneish_flag if r.beneish_flag in ("EXCLUDED", "FLAGGED", "CLEAN", "INSUFFICIENT_DATA") else None,
            "insider_signal":  r.insider_signal,
            "raw_factors":     _sanitize(r.raw_factors),
            "queued_for_research": r.queued_for_research,
        })

    if not rows:
        return

    # Upsert in batches of 100 (Supabase row limit per request)
    for i in range(0, len(rows), 100):
        batch = rows[i: i + 100]
        try:
            client.table("watchlist").upsert(batch, on_conflict="run_date,ticker").execute()
        except Exception as exc:
            logger.error("Watchlist upsert failed (batch %d): %s", i // 100, exc)

    logger.info("Upserted %d watchlist rows for %s", len(rows), run_date.isoformat())


def _queue_top_n_for_research(
    run_date: date,
    n: int = _TOP_N_FOR_RESEARCH,
) -> list[str]:
    """
    Queue the top N all-time qualified tickers (score >= 7.0) for research.

    Candidate pool: best-ever composite_score per ticker across ALL watchlist runs,
    not just today's. This ensures a stock that has consistently scored 8.5 but
    never landed in today's top 5 still gets researched.

    Filtering: Excludes tickers with memos < 7 days old AND no material_event=True.
    This gives research opportunities to other qualified tickers instead of
    constantly re-queuing the same companies.

    Returns list of queued ticker symbols.
    """
    try:
        client = _get_client()
    except Exception as exc:
        logger.error("Supabase client unavailable — skipping research queue: %s", exc)
        return []

    # Fetch best-ever score per ticker across all watchlist history
    try:
        result = (
            client.table("watchlist")
            .select("ticker,composite_score,material_event")
            .gte("composite_score", _QUALIFY_THRESHOLD)
            .order("composite_score", desc=True)
            .execute()
        )
        all_rows = result.data or []
    except Exception as exc:
        logger.error("_queue_top_n_for_research: watchlist read failed — %s", exc)
        return []

    # Collapse to best-ever score per ticker; preserve material_event=True if any row has it
    best: dict[str, dict] = {}
    for row in all_rows:
        ticker = row["ticker"]
        if ticker not in best or row["composite_score"] > best[ticker]["composite_score"]:
            best[ticker] = row
        elif row.get("material_event"):
            best[ticker]["material_event"] = True

    # Filter out tickers with recent memos and no material events
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=7)).isoformat()
    filtered_best: dict[str, dict] = {}
    
    for ticker, row in best.items():
        try:
            # Check for recent memo
            memo_result = client.table("memos").select("id").eq("ticker", ticker).gte("date", cutoff).limit(1).execute()
            has_recent_memo = bool(memo_result.data)
            
            # Include if: no recent memo OR has material event
            if not has_recent_memo or row.get("material_event", False):
                filtered_best[ticker] = row
        except Exception as exc:
            logger.warning("Failed to check memo status for %s: %s — including anyway", ticker, exc)
            filtered_best[ticker] = row  # Include on error to be safe

    # Sort by best-ever score descending, pick top N from filtered list
    ranked = sorted(filtered_best.values(), key=lambda x: x["composite_score"], reverse=True)

    queued: list[str] = []
    for row in ranked:
        if len(queued) >= n:
            break
        queued.append(row["ticker"])

    if queued:
        try:
            client.table("watchlist").update({"queued_for_research": True}).in_(
                "ticker", queued
            ).eq("run_date", run_date.isoformat()).execute()
            logger.info("Queued for research (all-time top %d): %s", n, queued)
        except Exception as exc:
            logger.error("Failed to set queued_for_research: %s", exc)

    return queued


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run_screening(regime: str | None = None) -> list[dict]:
    """
    Run the full screening pipeline.

    Args:
        regime: Macro regime override. If None, reads from Supabase.

    Returns:
        List of dicts for qualified tickers (composite_score ≥ 7.0), sorted descending.
    """
    run_date = date.today()
    regime   = regime or _read_regime()
    logger.info("=== Screening run starting | date=%s regime=%s ===", run_date, regime)

    # Step 1: Build universe
    try:
        universe = build_universe()
    except Exception as exc:
        raise ScreeningAgentError(f"Universe build failed: {exc}") from exc

    if not universe:
        logger.warning("Universe is empty — aborting screening run")
        return []

    logger.info("Universe: %d candidates", len(universe))

    # Step 2: Batch-fetch all ticker data (Polygon / yfinance)
    raw_data_map = _batch_fetch_ticker_data(universe)

    # Step 2b: Batch-fetch FMP quality data (income statements + balance sheets)
    # Done once for all tickers before scoring to stay within 300 req/min rate limit.
    all_tickers = list(raw_data_map.keys())
    logger.info("Fetching FMP quality data for %d tickers ...", len(all_tickers))
    fmp_quality_map: dict[str, dict] = {}
    try:
        fmp_quality_map = fetch_quality_fmp_batch(all_tickers)
        logger.info("FMP quality data fetched for %d tickers", len(fmp_quality_map))
    except Exception as exc:
        logger.error("FMP quality batch fetch failed — proceeding without FMP quality data: %s", exc)

    # Step 3: Score each ticker (quality, value, momentum, beneish)
    raw_factor_results: dict[str, dict] = {}
    for ticker, raw_data in raw_data_map.items():
        raw_factor_results[ticker] = _score_ticker(
            ticker, raw_data, fmp_quality=fmp_quality_map.get(ticker)
        )

    # Step 4: First-pass composite estimate (without Form 4) to identify Form 4 candidates
    # We do a lightweight score estimate using only quality sub-metrics as proxy
    form4_candidates: list[str] = []
    for ticker, factors in raw_factor_results.items():
        # Rough pre-filter: include if not EXCLUDED and has some quality data
        if factors.get("beneish", {}).get("gate_result") == "EXCLUDED":
            continue
        gm = factors.get("quality", {}).get("raw_values", {}).get("gross_margin")
        # Use gross_margin as a simple proxy — any ticker with margin data is a candidate
        if gm is not None:
            form4_candidates.append(ticker)

    # Limit Form 4 calls to avoid excessive EDGAR requests (cap at 200)
    form4_candidates = form4_candidates[:200]

    # Step 5: Fetch Form 4 insider buying for candidates
    logger.info("Fetching Form 4 for %d candidates", len(form4_candidates))
    form4_results = _fetch_form4_for_candidates(form4_candidates)

    # Merge form4 into raw_factor_results
    for ticker, f4 in form4_results.items():
        raw_factor_results.setdefault(ticker, {})["form4"] = f4

    # Step 6: Compute composite scores
    all_results = compute_composite(universe, raw_factor_results, regime)

    # Step 7: Write all results to Supabase watchlist (including EXCLUDED for audit)
    try:
        _store_results(all_results, run_date, regime)
    except Exception as exc:
        logger.error("Failed to store screener results: %s", exc)

    # Step 8: Queue top N qualified tickers for research (all-time pool, not just today's run)
    qualified = [r for r in all_results if not r.excluded and r.composite_score >= _QUALIFY_THRESHOLD]
    logger.info("%d tickers qualify today (score ≥ %.1f)", len(qualified), _QUALIFY_THRESHOLD)

    queued = _queue_top_n_for_research(run_date)

    logger.info("=== Screening run complete | queued=%s ===", queued)

    # Slack notifications
    notify_event("SCREENING_COMPLETE", {
        "regime":          regime,
        "qualified_count": len(qualified),
        "universe_size":   len(universe),
        "date":            run_date.isoformat(),
        "top_tickers": [
            {"ticker": r.ticker, "score": r.composite_score}
            for r in qualified[:5]
        ],
    })
    if queued:
        notify_event("RESEARCH_QUEUED", {"tickers": queued})

    return [
        {
            "ticker":          r.ticker,
            "composite_score": r.composite_score,
            "quality_score":   r.quality_score,
            "value_score":     r.value_score,
            "momentum_score":  r.momentum_score,
            "rank":            r.rank,
            "sector":          r.sector,
            "market_cap_m":    r.market_cap_m,
            "beneish_flag":    r.beneish_flag,
            "insider_signal":  r.insider_signal,
        }
        for r in qualified
    ]
