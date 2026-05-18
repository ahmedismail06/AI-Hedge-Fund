"""
One-shot backfill: run trigger scorer on BENEISH_EXCLUDED short_candidates rows
that have no trigger_count yet.

Usage:
    python scripts/backfill_beneish_trigger_scores.py
    python scripts/backfill_beneish_trigger_scores.py --run-date 2026-05-18
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import date

from dotenv import load_dotenv

load_dotenv()

from backend.memory.vector_store import _get_client
from backend.screener.short_trigger_scorer import score_triggers
from backend.screener.short_universe import (
    _fetch_key_metrics_ttm,
    _fetch_quarterly_history,
    _fetch_runway,
    _fetch_short_interest_pct,
)

import os

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _fetch_unscored_rows(run_date: date | None) -> list[dict]:
    client = _get_client()
    q = (
        client.table("short_candidates")
        .select("ticker,run_date,sector,market_cap_m,adv_k,beneish_m_score,beneish_flag")
        .eq("source", "BENEISH_EXCLUDED")
        .is_("trigger_count", "null")
    )
    if run_date:
        q = q.eq("run_date", run_date.isoformat())
    result = q.execute()
    return result.data or []


def _score_and_update(rows: list[dict]) -> None:
    fmp_key = os.getenv("FMP_API_KEY")
    polygon_key = os.getenv("POLYGON_API_KEY")
    if not fmp_key:
        raise SystemExit("FMP_API_KEY not set")

    universe_rows: list[dict] = []
    beneish_lookup: dict[str, dict] = {}

    for row in rows:
        ticker = row["ticker"]
        logger.info("Fetching trigger inputs for %s …", ticker)
        revenue_q, gm_q, shares_q = _fetch_quarterly_history(ticker, fmp_key)
        time.sleep(0.15)
        km = _fetch_key_metrics_ttm(ticker, fmp_key)
        time.sleep(0.15)
        _, fcf_ttm, runway = _fetch_runway(ticker, fmp_key)
        time.sleep(0.15)
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
    logger.info("Scored %d tickers", len(scored))

    client = _get_client()
    for result in scored:
        run_date_str = next(
            r["run_date"] for r in rows if r["ticker"] == result.ticker
        )
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
        }).eq("run_date", run_date_str).eq("ticker", result.ticker).execute()
        logger.info(
            "Updated %s: trigger_count=%d triggers=%s",
            result.ticker, result.trigger_count, result.triggers_fired,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-date", help="ISO date (default: all unscored rows)")
    args = parser.parse_args()

    run_date = date.fromisoformat(args.run_date) if args.run_date else None
    rows = _fetch_unscored_rows(run_date)
    if not rows:
        logger.info("No unscored BENEISH_EXCLUDED rows found — nothing to do")
        return
    logger.info("Found %d unscored rows: %s", len(rows), [r["ticker"] for r in rows])
    _score_and_update(rows)
    logger.info("Backfill complete")


if __name__ == "__main__":
    main()
