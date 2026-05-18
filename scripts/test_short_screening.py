"""
Quick smoke test for the short screening pipeline.

Fetches the 20 most recent watchlist rows (mix of EXCLUDED, FLAGGED, CLEAN),
runs them through compute_short_scores(), prints ranked output, and
optionally upserts the top candidates to short_candidates.

Usage:
    python scripts/test_short_screening.py
    python scripts/test_short_screening.py --write   # also upsert to short_candidates
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from backend.memory.vector_store import _get_client
from backend.screener.short_scorer import compute_short_scores, select_short_candidates


def fetch_test_candidates(run_date: str, limit: int = 25) -> list[dict]:
    """Pull a representative sample: EXCLUDED rows + high-EV + FLAGGED + misc."""
    client = _get_client()

    # Grab a mix: some EXCLUDED (strongest bear signal), some FLAGGED, some CLEAN with
    # high EV multiples, and some with weak quality metrics
    result = (
        client.table("watchlist")
        .select("ticker,market_cap_m,sector,adv_k,beneish_m_score,beneish_flag,raw_factors")
        .eq("run_date", run_date)
        .not_.is_("raw_factors", "null")
        .limit(limit)
        .execute()
    )
    rows = result.data or []

    candidates = []
    for r in rows:
        candidates.append({
            "ticker":        r["ticker"],
            "market_cap_m":  r.get("market_cap_m"),
            "sector":        r.get("sector"),
            "adv_k":         r.get("adv_k"),
            "beneish_m_score": r.get("beneish_m_score"),
            "beneish_flag":  r.get("beneish_flag"),
            "raw_factors":   r.get("raw_factors") or {},
            "source":        "watchlist",
        })
    return candidates


def get_latest_run_date() -> str:
    client = _get_client()
    result = (
        client.table("watchlist")
        .select("run_date")
        .not_.is_("raw_factors", "null")
        .order("run_date", desc=True)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]["run_date"]
    return (date.today() - timedelta(days=1)).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="Upsert top candidates to short_candidates table")
    parser.add_argument("--limit", type=int, default=25, help="Number of watchlist rows to test with")
    parser.add_argument("--regime", default="Transitional")
    args = parser.parse_args()

    run_date = get_latest_run_date()
    print(f"\nUsing run_date: {run_date} | regime: {args.regime} | sample size: {args.limit}")

    candidates = fetch_test_candidates(run_date, limit=args.limit)
    print(f"Fetched {len(candidates)} candidates\n")

    if not candidates:
        print("No candidates found — aborting.")
        return

    # Score
    scored = compute_short_scores(candidates, regime=args.regime)

    # Print full rankings
    scored.sort(key=lambda r: r.short_score, reverse=True)
    print(f"{'Rank':<5} {'Ticker':<8} {'Score':>6} {'Det':>5} {'OVal':>5} {'Accr':>5} {'Burn':>5} {'Beneish':<12} {'Sector'}")
    print("-" * 85)
    for i, r in enumerate(scored, 1):
        flag = r.beneish_flag or "?"
        print(
            f"{i:<5} {r.ticker:<8} {r.short_score:>6.3f} "
            f"{r.deterioration_score:>5.2f} {r.overvaluation_score:>5.2f} "
            f"{r.accruals_score:>5.2f} {r.cash_burn_score:>5.2f} "
            f"{flag:<12} {r.sector or 'Unknown'}"
        )

    # Show selection result
    selected = select_short_candidates(scored, max_candidates=5)
    print(f"\n=== Selected candidates (score ≥ 6.0, max 5, diversity gate) ===")
    if not selected:
        print("  None met the threshold.")
    else:
        for r in selected:
            print(f"  {r.ticker}: score={r.short_score:.3f} ({r.sector}) beneish={r.beneish_flag}")

    if args.write and selected:
        from backend.agents.short_screening_agent import _store_short_candidates
        _store_short_candidates(selected, date.fromisoformat(run_date), args.regime)
        print(f"\nWrote {len(selected)} rows to short_candidates (run_date={run_date})")

        # Verify
        client = _get_client()
        verify = (
            client.table("short_candidates")
            .select("ticker,short_score,beneish_flag,queued_for_research")
            .eq("run_date", run_date)
            .execute()
        )
        print(f"Verified in DB: {[r['ticker'] for r in (verify.data or [])]}")


if __name__ == "__main__":
    main()
