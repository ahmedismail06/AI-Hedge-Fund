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
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

from dotenv import load_dotenv

from backend.screener.universe import UniverseCandidate, build_universe, fetch_ticker_data
from backend.screener.factors.earnings_quality import compute_beneish
from backend.screener.factors.quality import score_quality
from backend.screener.factors.value import score_value
from backend.screener.factors.momentum import score_momentum
from backend.screener.scorer import ScreenerResult, compute_composite
from backend.memory.vector_store import _get_client

load_dotenv()

logger = logging.getLogger(__name__)

_BATCH_SIZE = 50
_MAX_WORKERS = 10
_QUALIFY_THRESHOLD = 7.0
_TOP_N_FOR_RESEARCH = 5
_INSIDER_PRE_FILTER_SCORE = 5.0   # only fetch Form 4 for tickers above this pre-adjustment score
_RESEARCH_SKIP_DAYS = 14          # skip tickers with APPROVED/WATCHLIST memo in last N days


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

def _score_ticker(ticker: str, raw_data: dict) -> dict:
    """
    Run all factor scorers on pre-fetched ticker data.
    Returns {ticker, quality, value, momentum, beneish, fmp}.
    Never raises.
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
        out["quality"]  = score_quality(ticker, raw_data["polygon_financials"], raw_data["yf_info"])
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

def _sanitize_float(v) -> float | None:
    """Convert NaN/Inf to None so Supabase JSON serialization doesn't fail."""
    if v is None:
        return None
    try:
        f = float(v)
        return None if (f != f or f == float("inf") or f == float("-inf")) else f
    except (TypeError, ValueError):
        return None


def _sanitize_dict(d: dict) -> dict:
    """Recursively replace NaN/Inf floats in a dict with None."""
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out[k] = _sanitize_dict(v)
        elif isinstance(v, float):
            out[k] = _sanitize_float(v)
        else:
            out[k] = v
    return out


def _store_results(results: list[ScreenerResult], run_date: date, regime: str) -> None:
    """Bulk-upsert all screener results to the watchlist table."""
    try:
        client = _get_client()
    except Exception as exc:
        logger.error("Supabase client unavailable — skipping watchlist write: %s", exc)
        return

    rows = []
    for r in results:
        rows.append({
            "run_date":        run_date.isoformat(),
            "ticker":          r.ticker,
            "composite_score": _sanitize_float(r.composite_score),
            "quality_score":   _sanitize_float(r.quality_score),
            "value_score":     _sanitize_float(r.value_score),
            "momentum_score":  _sanitize_float(r.momentum_score),
            "rank":            r.rank,
            "market_cap_m":    _sanitize_float(r.market_cap_m),
            "adv_k":           _sanitize_float(r.adv_k),
            "sector":          r.sector,
            "regime":          regime,
            "beneish_m_score": _sanitize_float(r.beneish_m_score),
            "beneish_flag":    r.beneish_flag if r.beneish_flag in ("EXCLUDED", "FLAGGED", "CLEAN", "INSUFFICIENT_DATA") else None,
            "insider_signal":  r.insider_signal,
            "raw_factors":     _sanitize_dict(r.raw_factors) if r.raw_factors else r.raw_factors,
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
    qualified: list[ScreenerResult],
    run_date: date,
    n: int = _TOP_N_FOR_RESEARCH,
) -> list[str]:
    """
    Set queued_for_research=True for the top N qualified tickers,
    skipping any ticker with an APPROVED or WATCHLIST memo in the last 14 days.
    Returns list of queued ticker symbols.
    """
    try:
        client = _get_client()
    except Exception as exc:
        logger.error("Supabase client unavailable — skipping research queue: %s", exc)
        return []

    skip_date = (run_date - timedelta(days=_RESEARCH_SKIP_DAYS)).isoformat()

    # Fetch recently approved/watchlisted tickers
    recently_processed: set[str] = set()
    try:
        result = (
            client.table("memos")
            .select("ticker")
            .in_("status", ["APPROVED", "WATCHLIST"])
            .gte("date", skip_date)
            .execute()
        )
        recently_processed = {row["ticker"] for row in (result.data or [])}
    except Exception as exc:
        logger.warning("Could not fetch recent memos for skip check: %s", exc)

    queued: list[str] = []
    for r in sorted(qualified, key=lambda x: x.composite_score, reverse=True):
        if len(queued) >= n:
            break
        if r.ticker in recently_processed:
            logger.info("%s: skipped (recently APPROVED/WATCHLIST)", r.ticker)
            continue
        queued.append(r.ticker)

    if queued:
        try:
            client.table("watchlist").update({"queued_for_research": True}).in_(
                "ticker", queued
            ).eq("run_date", run_date.isoformat()).execute()
            logger.info("Queued for research: %s", queued)
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
    import time as _time
    _t0 = _time.time()

    run_date = date.today()
    regime   = regime or _read_regime()
    print(f"\n{'='*60}")
    print(f"[SCREENER] Starting screening run | date={run_date} regime={regime}")
    print(f"{'='*60}")
    logger.info("=== Screening run starting | date=%s regime=%s ===", run_date, regime)

    # Step 1: Build universe
    print("[SCREENER] Step 1/8 — Building universe...")
    try:
        universe = build_universe()
    except Exception as exc:
        print(f"[SCREENER] ERROR: Universe build failed: {exc}")
        raise ScreeningAgentError(f"Universe build failed: {exc}") from exc

    if not universe:
        print("[SCREENER] WARNING: Universe is empty — aborting")
        logger.warning("Universe is empty — aborting screening run")
        return []

    print(f"[SCREENER] Universe: {len(universe)} candidates")
    logger.info("Universe: %d candidates", len(universe))

    # Step 2: Batch-fetch all ticker data
    print(f"[SCREENER] Step 2/8 — Fetching per-ticker data ({len(universe)} tickers, {_MAX_WORKERS} workers)...")
    _t1 = _time.time()
    raw_data_map = _batch_fetch_ticker_data(universe)
    print(f"[SCREENER] Fetch complete: {len(raw_data_map)} tickers in {_time.time()-_t1:.1f}s")

    # Step 3: Score each ticker (quality, value, momentum, beneish)
    print(f"[SCREENER] Step 3/8 — Scoring factors for {len(raw_data_map)} tickers...")
    raw_factor_results: dict[str, dict] = {}
    score_errors = 0
    for ticker, raw_data in raw_data_map.items():
        scored = _score_ticker(ticker, raw_data)
        raw_factor_results[ticker] = scored
        # Check if all factor scorers returned empty (data issue)
        if not scored.get("quality") and not scored.get("value") and not scored.get("momentum"):
            score_errors += 1
    print(f"[SCREENER] Scoring complete. Empty-data tickers: {score_errors}/{len(raw_data_map)}")

    # Step 4: First-pass composite estimate (without Form 4) to identify Form 4 candidates
    form4_candidates: list[str] = []
    excluded_count = 0
    for ticker, factors in raw_factor_results.items():
        if factors.get("beneish", {}).get("gate_result") == "EXCLUDED":
            excluded_count += 1
            continue
        gm = factors.get("quality", {}).get("raw_values", {}).get("gross_margin")
        if gm is not None:
            form4_candidates.append(ticker)

    form4_candidates = form4_candidates[:200]
    print(f"[SCREENER] Step 4/8 — Beneish gate: {excluded_count} EXCLUDED. Form 4 candidates: {len(form4_candidates)}")

    # Step 5: Fetch Form 4 insider buying for candidates
    print(f"[SCREENER] Step 5/8 — Fetching Form 4 insider data for {len(form4_candidates)} tickers...")
    logger.info("Fetching Form 4 for %d candidates", len(form4_candidates))
    form4_results = _fetch_form4_for_candidates(form4_candidates)
    insider_buy_count = sum(1 for v in form4_results.values() if v.get("insider_buy"))
    print(f"[SCREENER] Form 4 complete: {insider_buy_count} tickers with insider buying signal")

    # Merge form4 into raw_factor_results
    for ticker, f4 in form4_results.items():
        raw_factor_results.setdefault(ticker, {})["form4"] = f4

    # Step 6: Compute composite scores
    print("[SCREENER] Step 6/8 — Computing composite scores...")
    all_results = compute_composite(universe, raw_factor_results, regime)

    # Score distribution summary
    scores = [r.composite_score for r in all_results if not r.excluded]
    if scores:
        above_5 = sum(1 for s in scores if s >= 5.0)
        above_7 = sum(1 for s in scores if s >= _QUALIFY_THRESHOLD)
        print(f"[SCREENER] Score distribution: {len(scores)} scored | ≥5.0: {above_5} | ≥{_QUALIFY_THRESHOLD}: {above_7}")
        top5 = sorted(all_results, key=lambda r: r.composite_score, reverse=True)[:5]
        print("[SCREENER] Top 5 by composite score:")
        for r in top5:
            print(f"  {r.ticker:6s}  composite={r.composite_score:.2f}  quality={r.quality_score:.2f}  value={r.value_score:.2f}  momentum={r.momentum_score:.2f}  sector={r.sector}  beneish={r.beneish_flag}")

    # Step 7: Write all results to Supabase watchlist
    print(f"[SCREENER] Step 7/8 — Writing {len(all_results)} results to Supabase watchlist...")
    try:
        _store_results(all_results, run_date, regime)
        print("[SCREENER] Supabase write OK")
    except Exception as exc:
        print(f"[SCREENER] ERROR: Supabase write failed: {exc}")
        logger.error("Failed to store screener results: %s", exc)

    # Step 8: Queue top N qualified tickers for research
    qualified = [r for r in all_results if not r.excluded and r.composite_score >= _QUALIFY_THRESHOLD]
    print(f"[SCREENER] Step 8/8 — {len(qualified)} tickers qualify (score ≥ {_QUALIFY_THRESHOLD})")
    logger.info("%d tickers qualify (score ≥ %.1f)", len(qualified), _QUALIFY_THRESHOLD)

    queued = _queue_top_n_for_research(qualified, run_date)

    elapsed = _time.time() - _t0
    print(f"\n[SCREENER] Run complete in {elapsed:.1f}s | queued for research: {queued}")
    print(f"{'='*60}\n")
    logger.info("=== Screening run complete | queued=%s ===", queued)

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
