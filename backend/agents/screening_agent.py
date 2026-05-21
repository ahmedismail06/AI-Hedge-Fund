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

from backend.screener.universe import UniverseCandidate, build_universe, fetch_ticker_data, filter_by_profitability
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
_MAX_WORKERS = 3
_QUALIFY_THRESHOLD = 6.5
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
        out["beneish"]  = compute_beneish(ticker, raw_data["polygon_financials"], fmp_quality=fmp_quality)
    except Exception as exc:
        logger.warning("%s: beneish scorer failed: %s", ticker, exc)

    # ── Data-quality audit: count null sub-metrics across factors ─────────────
    # Persisted on the row so we can downstream-filter tickers whose composite
    # scores are based on near-empty inputs. Composer doesn't act on it yet.
    null_count = 0
    for factor_key in ("quality", "value", "momentum"):
        raw_values = out.get(factor_key, {}).get("raw_values", {}) or {}
        for sub_key, val in raw_values.items():
            # Skip non-numeric metadata fields (ev_type, ebitda_source, flags)
            if sub_key in ("ev_type", "ebitda_source", "is_profitable", "ocf_annualized"):
                continue
            if val is None:
                null_count += 1
    out["data_quality_nulls"] = null_count

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


# Sectors where Beneish is miscalibrated (no COGS / different accrual structure).
# REITs, banks, insurers, and utilities all generate noise M-scores.
_BENEISH_UNRELIABLE_SECTORS = {"Real Estate", "Financials", "Utilities"}
_BENEISH_MIN_ADV_K = 5_000.0      # $5M ADV floor for shortable names
_BENEISH_MAX_MISSING_FIELDS = 3   # M-score with 3+ missing inputs is low-confidence
_BENEISH_HEALTHY_ROIC = 0.15      # ROIC threshold for "fundamentals look healthy"

# Gates applied AFTER trigger-count backfill (decide queued_for_research=True).
# Beneish-EXCLUDED is a quarterly accounting signal — re-researching daily is
# wasted compute. Only requeue on a catalyst (new earnings) or periodic refresh.
_BENEISH_MIN_TRIGGER_COUNT  = 3    # mirror short_trigger_scorer.MIN_TRIGGERS
_BENEISH_MEMO_FRESH_DAYS    = 14   # skip if SHORT memo this recent
_BENEISH_MEMO_STALE_DAYS    = 90   # always refresh after this long
_BENEISH_MAX_QUEUED_PER_RUN = 5    # per-direction parity with _TOP_N_FOR_RESEARCH


def _beneish_enqueue_skip_reason(r: ScreenerResult) -> str | None:
    """
    Return a skip reason (or None to enqueue) for a Beneish-EXCLUDED candidate.

    Four false-positive shapes get filtered:

      G1 Sector ill-fit: Beneish was calibrated on industrial firms and produces
         meaningless scores on REITs/financials/utilities (no COGS, different
         accrual structure). Half the inputs are typically missing.
      G2 Low-confidence M-score: ≥3 of the 8 Beneish inputs missing means the
         neutral-substitution fallback dominates the score.
      G3 Illiquid: shorting requires real borrow and exit liquidity — same
         $5M ADV floor as the dedicated short universe.
      G4 Fundamentals contradict: if ROIC > 15% AND revenue growth > 0 AND
         FCF conversion ≥ 0 the business is healthy by other measures, so the
         M-score is almost certainly catching an accounting artifact, not fraud.
    """
    # G1 — sector
    if r.sector in _BENEISH_UNRELIABLE_SECTORS:
        return f"sector={r.sector} — Beneish miscalibrated"

    # G2 — Beneish input quality
    rf = r.raw_factors or {}
    beneish_dict = rf.get("beneish") or {}
    missing = beneish_dict.get("missing_fields") or []
    if isinstance(missing, list) and len(missing) >= _BENEISH_MAX_MISSING_FIELDS:
        return f"low-confidence M-score (missing={missing})"

    # G3 — ADV floor
    if r.adv_k is not None and r.adv_k < _BENEISH_MIN_ADV_K:
        return f"illiquid (adv_k=${r.adv_k:.0f}k < ${_BENEISH_MIN_ADV_K:.0f}k floor)"

    # G4 — fundamentals contradict the fraud signal
    quality = rf.get("quality") or {}
    roic = quality.get("roic")
    rev_growth = quality.get("revenue_growth_yoy")
    fcf_conv = quality.get("fcf_conversion")
    if (
        roic is not None and roic > _BENEISH_HEALTHY_ROIC
        and rev_growth is not None and rev_growth > 0
        and fcf_conv is not None and fcf_conv >= 0
    ):
        return (
            f"fundamentals healthy (ROIC={roic:.2f}, rev_growth={rev_growth:.2f}, "
            f"fcf_conv={fcf_conv:.2f}) — likely Beneish false positive"
        )

    return None


def _beneish_catalyst_skip_reason(client, ticker: str, today: date) -> str | None:
    """
    Decide whether a Beneish-EXCLUDED ticker should be queued for SHORT research.

    Beneish-EXCLUDED is a fundamental accounting signal — once researched, there
    is no value re-running the same memo daily. Re-trigger only on a catalyst.

    Returns None to queue, or a string reason to skip:
      - No prior SHORT memo                              → queue (first time)
      - SHORT memo < _BENEISH_MEMO_FRESH_DAYS old        → skip (stale-redundant)
      - 14d ≤ memo age < 90d, NO earnings since memo    → skip (stable signal)
      - 14d ≤ memo age < 90d, earnings since memo       → queue (catalyst)
      - SHORT memo ≥ _BENEISH_MEMO_STALE_DAYS old        → queue (periodic refresh)
    """
    try:
        memo_resp = (
            client.table("memos")
            .select("date")
            .eq("ticker", ticker)
            .eq("verdict", "SHORT")
            .order("date", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.warning("_beneish_catalyst_skip_reason: memo read failed for %s — %s; queuing", ticker, exc)
        return None

    if not memo_resp.data:
        return None  # first time — queue

    memo_date_raw = memo_resp.data[0].get("date")
    try:
        memo_date = date.fromisoformat(str(memo_date_raw)[:10])
    except (TypeError, ValueError):
        return None  # un-parseable memo date — fail open

    age_days = (today - memo_date).days
    if age_days < _BENEISH_MEMO_FRESH_DAYS:
        return f"SHORT memo {age_days}d old (< {_BENEISH_MEMO_FRESH_DAYS}d freshness gate)"
    if age_days >= _BENEISH_MEMO_STALE_DAYS:
        return None  # periodic refresh

    # 14 ≤ age < 90: require an earnings event since the memo
    try:
        ev_resp = (
            client.table("earnings_events")
            .select("event_date")
            .eq("ticker", ticker)
            .gt("event_date", memo_date.isoformat())
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.warning(
            "_beneish_catalyst_skip_reason: earnings_events read failed for %s — %s; queuing",
            ticker, exc,
        )
        return None

    if ev_resp.data:
        return None  # catalyst found — queue
    return f"SHORT memo {age_days}d old, no earnings catalyst since"


def _enqueue_beneish_excluded_as_shorts(
    results: list[ScreenerResult], run_date: date, regime: str,
) -> int:
    """
    Auto-enqueue Beneish-EXCLUDED tickers into short_candidates with source=BENEISH_EXCLUDED.

    EXCLUDED (M-score > -1.78) is the strongest short signal the long pipeline produces.
    These tickers are dead-on-arrival for longs but live wires for shorts — route them
    straight to the research queue with high priority.

    Quality gates: see _beneish_enqueue_skip_reason. False-positive shapes (REITs,
    half-missing M-score inputs, illiquid names, fundamentally healthy businesses)
    are filtered before reaching bear thesis.
    """
    try:
        client = _get_client()
    except Exception as exc:
        logger.error("Beneish short auto-enqueue: Supabase unavailable — %s", exc)
        return 0

    rows = []
    skipped: list[tuple[str, str]] = []
    for r in results:
        if r.beneish_flag != "EXCLUDED":
            continue
        skip_reason = _beneish_enqueue_skip_reason(r)
        if skip_reason is not None:
            skipped.append((r.ticker, skip_reason))
            continue
        rows.append({
            "run_date":            run_date.isoformat(),
            "ticker":              r.ticker,
            "source":              "BENEISH_EXCLUDED",
            "priority":            3,   # high — these are pre-qualified
            "evidence":            {
                "m_score":           r.beneish_m_score,
                "raw_factors":       r.raw_factors,
            },
            "market_cap_m":        r.market_cap_m,
            "sector":              r.sector,
            "adv_k":               r.adv_k,
            "beneish_m_score":     r.beneish_m_score,
            "beneish_flag":        "EXCLUDED",
            "raw_factors":         r.raw_factors,
            "queued_for_research": False,   # audit-only until trigger/catalyst gates pass below
            "regime":              regime,
        })

    if skipped:
        logger.info(
            "Beneish short auto-enqueue: filtered %d EXCLUDED rows as false positives: %s",
            len(skipped),
            ", ".join(f"{t} ({reason})" for t, reason in skipped[:10]),
        )

    if not rows:
        logger.info("Beneish short auto-enqueue: 0 candidates passed quality gates")
        return 0

    try:
        client.table("short_candidates").upsert(
            rows, on_conflict="run_date,ticker"
        ).execute()
        logger.info(
            "Beneish short auto-enqueue: %d EXCLUDED rows upserted (audit); applying trigger/catalyst gates",
            len(rows),
        )
    except Exception as exc:
        logger.error("Beneish short auto-enqueue: upsert failed — %s", exc)
        return 0

    # Compute trigger counts then apply trigger-count + catalyst gates before flipping queued_for_research.
    trigger_counts = _backfill_trigger_scores(rows, run_date)
    return _flip_queued_on_survivors(client, rows, trigger_counts, run_date)


def _flip_queued_on_survivors(
    client,
    upserted_rows: list[dict],
    trigger_counts: dict[str, int],
    run_date: date,
) -> int:
    """
    Final gate for Beneish-EXCLUDED short auto-enqueue:
      1. trigger_count ≥ _BENEISH_MIN_TRIGGER_COUNT (drop Beneish-only weak signals)
      2. catalyst gate (drop redundant re-research; require event when 14d≤age<90d)
      3. cap at _BENEISH_MAX_QUEUED_PER_RUN by trigger_count desc

    Sets queued_for_research=True on survivors. Returns the count actually queued.
    """
    today = date.today()
    gated: list[tuple[str, int, str | None]] = []  # (ticker, trigger_count, skip_reason)
    for row in upserted_rows:
        ticker = row["ticker"]
        tc = trigger_counts.get(ticker)

        if tc is None or tc < _BENEISH_MIN_TRIGGER_COUNT:
            gated.append((ticker, tc or 0, f"trigger_count={tc} < {_BENEISH_MIN_TRIGGER_COUNT}"))
            continue

        catalyst_skip = _beneish_catalyst_skip_reason(client, ticker, today)
        if catalyst_skip is not None:
            gated.append((ticker, tc, catalyst_skip))
            continue

        gated.append((ticker, tc, None))  # passes all gates

    survivors = sorted(
        [(t, tc) for t, tc, skip in gated if skip is None],
        key=lambda x: x[1],
        reverse=True,
    )[:_BENEISH_MAX_QUEUED_PER_RUN]

    survivor_tickers = [t for t, _ in survivors]
    dropped = [(t, skip) for t, _, skip in gated if skip is not None]
    if dropped:
        logger.info(
            "Beneish short auto-enqueue: %d rows audit-only (gated): %s",
            len(dropped),
            ", ".join(f"{t} ({reason})" for t, reason in dropped[:10]),
        )

    if not survivor_tickers:
        logger.info("Beneish short auto-enqueue: 0 candidates queued for research after gates")
        return 0

    try:
        client.table("short_candidates").update(
            {"queued_for_research": True}
        ).eq("run_date", run_date.isoformat()).in_("ticker", survivor_tickers).execute()
    except Exception as exc:
        logger.error("Beneish short auto-enqueue: queue flip failed — %s", exc)
        return 0

    logger.info(
        "Beneish short auto-enqueue: queued %d for SHORT research: %s",
        len(survivor_tickers),
        ", ".join(f"{t}(tc={tc})" for t, tc in survivors),
    )
    return len(survivor_tickers)


def _backfill_trigger_scores(rows: list[dict], run_date: date) -> dict[str, int]:
    """
    Fetch trigger-scorer inputs for BENEISH_EXCLUDED rows and write back
    trigger_count + triggers_fired to short_candidates.

    BENEISH_EXCLUDED tickers come from the long screener ($50M–$2B cap) and
    won't be in the short universe cache ($2B–$20B), so data is fetched fresh.
    T4 (Beneish) is already known from the ScreenerResult — no re-computation.

    Returns {ticker: trigger_count} for the rows that were scored. Tickers
    skipped (e.g. FMP missing) are omitted; caller treats absent → trigger=0.
    """
    import time as _time
    from backend.screener.short_trigger_scorer import score_triggers
    from backend.screener.short_universe import (
        _fetch_quarterly_history,
        _fetch_key_metrics_ttm,
        _fetch_runway,
        _fetch_short_interest_pct,
    )

    fmp_key = os.getenv("FMP_API_KEY")
    polygon_key = os.getenv("POLYGON_API_KEY")
    if not fmp_key:
        logger.warning("Beneish trigger backfill: FMP_API_KEY missing — skipping")
        return {}

    universe_rows: list[dict] = []
    beneish_lookup: dict[str, dict] = {}

    for row in rows:
        ticker = row["ticker"]
        revenue_q, gm_q, shares_q = _fetch_quarterly_history(ticker, fmp_key)
        _time.sleep(0.15)
        km = _fetch_key_metrics_ttm(ticker, fmp_key)
        _time.sleep(0.15)
        _, fcf_ttm, runway = _fetch_runway(ticker, fmp_key)
        _time.sleep(0.15)
        si_pct = _fetch_short_interest_pct(ticker, polygon_key) if polygon_key else None

        universe_rows.append({
            "ticker":             ticker,
            "sector":             row.get("sector"),
            "market_cap_m":       row.get("market_cap_m"),
            "adv_k":              row.get("adv_k"),
            "revenue_q":          revenue_q,
            "gross_margin_q":     gm_q,
            "shares_diluted_q":   shares_q,
            "fcf_ttm":            fcf_ttm,
            "cash_runway_months": runway,
            "ev_to_sales_ttm":    km.get("ev_to_sales_ttm"),
            "debt_to_ebitda_ttm": km.get("debt_to_ebitda_ttm"),
            "short_interest_pct": si_pct,
        })
        beneish_lookup[ticker] = {
            "m_score":    row.get("beneish_m_score"),
            "gate_result": row.get("beneish_flag"),
        }

    scored = score_triggers(universe_rows, beneish_lookup=beneish_lookup)
    if not scored:
        logger.info("Beneish trigger backfill: no trigger results returned")
        return {}

    try:
        client = _get_client()
        for result in scored:
            client.table("short_candidates").update({
                "trigger_count":  result.trigger_count,
                "triggers_fired": result.triggers_fired,
                "evidence": {
                    "m_score":            beneish_lookup.get(result.ticker, {}).get("m_score"),
                    "triggers_fired":     result.triggers_fired,
                    "triggers_evaluated": result.triggers_evaluated,
                    "trigger_count":      result.trigger_count,
                    "raw":                result.evidence,
                    "short_interest_pct": result.short_interest_pct,
                },
            }).eq("run_date", run_date.isoformat()).eq("ticker", result.ticker).execute()
        logger.info("Beneish trigger backfill: scored %d/%d tickers", len(scored), len(rows))
    except Exception as exc:
        logger.error("Beneish trigger backfill: update failed — %s", exc)

    return {r.ticker: r.trigger_count for r in scored}


def _queue_top_n_for_research(
    run_date: date,
    n: int = _TOP_N_FOR_RESEARCH,
) -> list[str]:
    """
    Queue the top N qualified tickers from today's run (score >= _QUALIFY_THRESHOLD) for research.

    Candidate pool: today's watchlist rows only. Using all-time best scores caused
    tickers with stale high scores to be queued on days where their current score
    fell below threshold.

    Filtering: Excludes tickers with memos < 7 days old AND no material_event=True.

    Returns list of queued ticker symbols.
    """
    try:
        client = _get_client()
    except Exception as exc:
        logger.error("Supabase client unavailable — skipping research queue: %s", exc)
        return []

    # Fetch today's qualifying rows only
    try:
        result = (
            client.table("watchlist")
            .select("ticker,composite_score,material_event")
            .eq("run_date", run_date.isoformat())
            .gte("composite_score", _QUALIFY_THRESHOLD)
            .order("composite_score", desc=True)
            .execute()
        )
        all_rows = result.data or []
    except Exception as exc:
        logger.error("_queue_top_n_for_research: watchlist read failed — %s", exc)
        return []

    # Filter out tickers with recent memos and no material events
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=7)).isoformat()
    filtered: list[dict] = []

    for row in all_rows:
        ticker = row["ticker"]
        try:
            deferred_result = client.table("memos").select("id").eq("ticker", ticker).eq("status", "DEFERRED").limit(1).execute()
            if deferred_result.data:
                logger.debug("_queue_top_n_for_research: skipping %s — memo is DEFERRED", ticker)
                continue

            memo_result = client.table("memos").select("id").eq("ticker", ticker).gte("date", cutoff).limit(1).execute()
            has_recent_memo = bool(memo_result.data)

            if not has_recent_memo or row.get("material_event", False):
                filtered.append(row)
        except Exception as exc:
            logger.warning("Failed to check memo status for %s: %s — including anyway", ticker, exc)
            filtered.append(row)

    queued = [row["ticker"] for row in filtered[:n]]

    if queued:
        try:
            client.table("watchlist").update({"queued_for_research": True}).in_(
                "ticker", queued
            ).eq("run_date", run_date.isoformat()).execute()
            logger.info("Queued for research (today's top %d, score ≥ %.1f): %s", n, _QUALIFY_THRESHOLD, queued)
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

        # Merge quality data (income_statement, balance_sheet) INTO the existing fmp dict.
        # Use .update() — not assignment — so the rich fetch_fmp() fields (market_cap,
        # long_term_debt, cash, ttm_operating_cash_flow, ev_ebitda_fmp, price_book_fmp,
        # consensus_eps_*, etc.) are preserved for the value and momentum scorers.
        for ticker, quality_data in fmp_quality_map.items():
            if ticker in raw_data_map:
                raw_data_map[ticker].setdefault("fmp", {}).update(quality_data)
    except Exception as exc:
        logger.error("FMP quality batch fetch failed — proceeding without FMP quality data: %s", exc)

    # Step 2c: Apply profitability pre-filter
    universe = filter_by_profitability(universe, raw_data_map)
    # Re-sync raw_data_map to filtered universe
    eligible_tickers = {c.ticker for c in universe}
    raw_data_map = {t: d for t, d in raw_data_map.items() if t in eligible_tickers}

    # Step 2d: Backfill fmp.market_cap from the universe candidate when fetch returned None.
    # The universe builder already cached market_cap via Polygon detail endpoint (24h TTL);
    # without this, a transient FMP /profile + Polygon /v3/reference/tickers failure leaves
    # market_cap=None which cascades to ev/p_fcf/price_book=None → value_score defaults to
    # neutral 5.0, silently under-ranking high-quality tickers (e.g. CODA, BODI).
    cand_by_ticker = {c.ticker: c for c in universe}
    for ticker, raw_data in raw_data_map.items():
        fmp = raw_data.setdefault("fmp", {})
        if fmp.get("market_cap") is None:
            cand = cand_by_ticker.get(ticker)
            if cand and cand.market_cap_m:
                fmp["market_cap"] = float(cand.market_cap_m) * 1_000_000
                fmp["market_cap_source"] = "universe_candidate_fallback"
                logger.info(
                    "%s: market_cap backfilled from universe candidate ($%.1fM) — FMP/Polygon both returned None",
                    ticker, cand.market_cap_m,
                )

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

    # Step 7b: Auto-enqueue Beneish-EXCLUDED tickers to short_candidates for bear-thesis research
    try:
        _enqueue_beneish_excluded_as_shorts(all_results, run_date, regime)
    except Exception as exc:
        logger.error("Beneish short auto-enqueue failed: %s", exc)

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
