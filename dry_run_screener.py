#!/usr/bin/env python3
"""
Screener dry-run diagnostic.
Runs the real pipeline against live Polygon/yfinance data but does NOT write to Supabase.

Usage:
    python dry_run_screener.py                     # universe build + 30-ticker stratified sample
    python dry_run_screener.py --sample 10         # smaller sample (faster)
    python dry_run_screener.py --universe-only     # just universe build + analyst stats
    python dry_run_screener.py --tickers AAPL MSFT # score specific tickers (skips universe build)
    python dry_run_screener.py --regime Risk-Off   # use a different regime

What this checks:
  1. Universe size (not 3, not 400+)
  2. Sector distribution and sample ticker names
  3. Analyst count distribution — what % passed with count=None
  4. Per-ticker factor data completeness (how many of 5Q/3V/3M raw_values are non-None)
  5. Silent 5.0 detection — tickers where all raw_values=None score as neutral; flags them
  6. Score distribution in sample
  7. Tickers that would qualify (composite >= 7.0) in the sample
"""

import argparse
import logging
import random
import sys
from datetime import date

from dotenv import load_dotenv

load_dotenv()

# Silence noisy sub-loggers before importing anything
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    stream=sys.stdout,
)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("peewee").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger("dry_run")


# ── Formatting helpers ────────────────────────────────────────────────────────

def _bar(count: int, max_count: int, width: int = 20) -> str:
    filled = int(width * count / max(max_count, 1))
    return "█" * filled + "░" * (width - filled)


def _fmt_opt(v, fmt=".2f") -> str:
    return f"{v:{fmt}}" if v is not None else "None"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Screener dry-run diagnostics")
    parser.add_argument("--sample", type=int, default=30,
                        help="Number of tickers to fully score (default 30)")
    parser.add_argument("--universe-only", action="store_true",
                        help="Only build universe; skip scoring")
    parser.add_argument("--tickers", nargs="+", metavar="TICKER",
                        help="Score specific tickers (skips universe build)")
    parser.add_argument("--regime", default="Risk-On",
                        choices=["Risk-On", "Risk-Off", "Transitional", "Stagflation"],
                        help="Regime to use for composite scoring (default Risk-On)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for stratified sample (default 42)")
    args = parser.parse_args()

    # ── Step 1: Universe ──────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("STEP 1: BUILD UNIVERSE")
    print("=" * 72)

    from backend.screener.universe import UniverseCandidate, build_universe, fetch_ticker_data

    if args.tickers:
        print(f"\n[--tickers] Skipping universe build. Using {len(args.tickers)} provided tickers.")
        print("  (Note: sector and market_cap will be fetched from yfinance info in --tickers mode)")
        from backend.screener.universe import _sic_to_sector
        tmp_cands = []
        for t in args.tickers:
            ticker = t.upper()
            # Try yfinance for sector / market cap
            import yfinance as yf
            sector = "Unknown"
            mktcap_m = 0.0
            try:
                info = yf.Ticker(ticker).info or {}
                mktcap_m = (info.get("marketCap") or 0) / 1_000_000
                # Map yfinance sector to our sector labels (approximation)
                yf_sector = info.get("sector", "")
                if yf_sector == "Healthcare":
                    sector = "Healthcare"
                elif yf_sector == "Industrials":
                    sector = "Industrials"
                elif yf_sector in ("Technology", "Communication Services"):
                    sector = "SaaS"
            except Exception:
                pass
            tmp_cands.append(UniverseCandidate(ticker=ticker, market_cap_m=round(mktcap_m, 2), sector=sector))
        universe = tmp_cands
    else:
        try:
            universe = build_universe()
        except Exception as exc:
            print(f"\n[FATAL] build_universe() raised: {exc}")
            sys.exit(1)

        if not universe:
            print("\n[FATAL] Universe is empty — check POLYGON_API_KEY and connectivity.")
            sys.exit(1)

        # ── Universe stats ────────────────────────────────────────────────────
        sector_counts: dict[str, int] = {}
        for c in universe:
            sector_counts[c.sector] = sector_counts.get(c.sector, 0) + 1

        analyst_none   = sum(1 for c in universe if c.analyst_count is None)
        analyst_zero   = sum(1 for c in universe if c.analyst_count == 0)
        analyst_over_5 = [c for c in universe if c.analyst_count is not None and c.analyst_count > 5]
        analyst_dist: dict = {}
        for c in universe:
            k = c.analyst_count if c.analyst_count is not None else "None"
            analyst_dist[k] = analyst_dist.get(k, 0) + 1

        mcap_vals = sorted(c.market_cap_m for c in universe)
        adv_vals  = sorted(c.adv_k for c in universe if c.adv_k is not None)

        print(f"\nUniverse size: {len(universe)} candidates")
        print(f"\nSector breakdown:")
        for sector in sorted(sector_counts):
            n = sector_counts[sector]
            pct = n / len(universe) * 100
            print(f"  {sector:<15} {n:4d}  ({pct:.1f}%)  {_bar(n, max(sector_counts.values()))}")

        if mcap_vals:
            mid = len(mcap_vals) // 2
            print(f"\nMarket cap ($M):  min={mcap_vals[0]:.0f}  "
                  f"median={mcap_vals[mid]:.0f}  max={mcap_vals[-1]:.0f}")
        if adv_vals:
            mid = len(adv_vals) // 2
            print(f"ADV ($K):         min={adv_vals[0]:.0f}  "
                  f"median={adv_vals[mid]:.0f}  max={adv_vals[-1]:.0f}")

        print(f"\nAnalyst count distribution (all passed ≤5 filter):")
        for k in sorted(analyst_dist.keys(), key=lambda x: (x == "None", x if x != "None" else 0)):
            bar = _bar(analyst_dist[k], max(analyst_dist.values()), width=15)
            label = "(yfinance returned None — allowed through)" if k == "None" else ""
            print(f"  {str(k):>6}: {analyst_dist[k]:4d}  {bar}  {label}")

        print(f"\n[CHECK] Universe health:")
        if len(universe) < 20:
            print(f"  [WARN] Universe suspiciously small: {len(universe)} (expected 50–800)")
        elif len(universe) > 1200:
            print(f"  [WARN] Universe suspiciously large: {len(universe)} (expected 50–800)")
        else:
            print(f"  [OK]   Size {len(universe)} is in the expected range")

        if analyst_over_5:
            print(f"  [WARN] {len(analyst_over_5)} tickers slipped through with analyst_count > 5: "
                  f"{[c.ticker for c in analyst_over_5[:10]]}")
        else:
            print(f"  [OK]   No tickers with analyst_count > 5 slipped through")

        analyst_none_pct = analyst_none / len(universe) * 100
        if analyst_none_pct > 60:
            print(f"  [WARN] {analyst_none:.0f}% of tickers have analyst_count=None "
                  f"— analyst filter may be underperforming")
        else:
            print(f"  [OK]   {analyst_none_pct:.0f}% of tickers have None analyst count "
                  f"(expected for micro-caps with no coverage)")

        bad = [c.ticker for c in universe if len(c.ticker) > 5 or not c.ticker.replace(".", "").isalpha()]
        if bad:
            print(f"  [WARN] Suspicious ticker symbols (long/non-alpha): {bad[:15]}")
        else:
            print(f"  [OK]   All ticker symbols look clean")

        # Sample tickers by sector
        print(f"\nSample tickers (first 6 per sector, format: TICK($MCap,Analysts)):")
        seen: dict[str, list[str]] = {}
        for c in universe:
            if c.sector not in seen:
                seen[c.sector] = []
            if len(seen[c.sector]) < 6:
                a = str(c.analyst_count) if c.analyst_count is not None else "?"
                seen[c.sector].append(f"{c.ticker}(${c.market_cap_m:.0f}M,{a}a)")
        for sector, samples in seen.items():
            print(f"  {sector:<15}: {', '.join(samples)}")

        if args.universe_only:
            print("\n[--universe-only] Done.\n")
            return

    # ── Step 2: Pick sample ───────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print(f"STEP 2: FETCH + SCORE SAMPLE  (n={args.sample}, regime={args.regime})")
    print("=" * 72)

    if args.tickers:
        sample_cands = universe
    else:
        random.seed(args.seed)
        # Stratified sample proportional to sector sizes
        sector_pools: dict[str, list[UniverseCandidate]] = {}
        for c in universe:
            sector_pools.setdefault(c.sector, []).append(c)
        for pool in sector_pools.values():
            random.shuffle(pool)

        per_sector = max(1, args.sample // len(sector_pools))
        sample_cands: list[UniverseCandidate] = []
        for pool in sector_pools.values():
            sample_cands.extend(pool[:per_sector])

        extras = [c for c in universe if c not in sample_cands]
        random.shuffle(extras)
        sample_cands.extend(extras[: args.sample - len(sample_cands)])
        sample_cands = sample_cands[:args.sample]

    print(f"\nScoring {len(sample_cands)} tickers:")
    print(f"  {', '.join(c.ticker for c in sample_cands)}\n")

    from backend.screener.factors.quality import score_quality
    from backend.screener.factors.value import score_value
    from backend.screener.factors.momentum import score_momentum
    from backend.screener.factors.earnings_quality import compute_beneish
    from backend.screener.scorer import compute_composite

    raw_factor_results: dict[str, dict] = {}
    per_ticker_stats: dict[str, dict] = {}  # for diagnostics

    for i, cand in enumerate(sample_cands):
        ticker = cand.ticker
        print(f"  [{i+1:2d}/{len(sample_cands)}] {ticker:<8}", end=" ", flush=True)

        try:
            raw = fetch_ticker_data(ticker)
        except Exception as exc:
            print(f"  FETCH FAILED: {exc}")
            continue

        out: dict = {
            "ticker":   ticker,
            "quality":  {},
            "value":    {},
            "momentum": {},
            "beneish":  {"gate_result": "INSUFFICIENT_DATA", "m_score": None, "missing_fields": []},
            "fmp":      raw.get("fmp", {}),
            "form4":    {"insider_buy": False},
        }
        stats = {"q_nonnull": 0, "v_nonnull": 0, "m_nonnull": 0, "errors": []}

        # Sub-metric keys (only count actual scoring inputs, not metadata extras)
        _Q_KEYS = {"gross_margin", "revenue_growth_yoy", "roe", "debt_to_equity", "eps_beat_rate"}
        _V_KEYS = {"ev_multiple", "p_fcf", "price_book"}
        _M_KEYS = {"price_12_1", "price_6_1", "eps_revision"}

        try:
            out["quality"] = score_quality(ticker, raw["polygon_financials"], raw["yf_info"])
            rv = out["quality"].get("raw_values", {})
            stats["q_nonnull"] = sum(1 for k in _Q_KEYS if rv.get(k) is not None)
        except Exception as exc:
            stats["errors"].append(f"Q:{exc}")

        try:
            out["value"] = score_value(ticker, raw["polygon_financials"], raw["fmp"])
            rv = out["value"].get("raw_values", {})
            stats["v_nonnull"] = sum(1 for k in _V_KEYS if rv.get(k) is not None)
        except Exception as exc:
            stats["errors"].append(f"V:{exc}")

        try:
            out["momentum"] = score_momentum(ticker, raw["price_history"], raw["fmp"])
            rv = out["momentum"].get("raw_values", {})
            stats["m_nonnull"] = sum(1 for k in _M_KEYS if rv.get(k) is not None)
        except Exception as exc:
            stats["errors"].append(f"M:{exc}")

        try:
            out["beneish"] = compute_beneish(ticker, raw["polygon_financials"])
        except Exception as exc:
            stats["errors"].append(f"B:{exc}")

        raw_factor_results[ticker] = out
        per_ticker_stats[ticker] = stats

        total_nonnull = stats["q_nonnull"] + stats["v_nonnull"] + stats["m_nonnull"]
        beneish_flag = out["beneish"].get("gate_result", "?")
        err_str = f"  [ERRORS: {'; '.join(stats['errors'])}]" if stats["errors"] else ""
        sparse_warn = " [SPARSE]" if total_nonnull == 0 else ""
        print(
            f"Q:{stats['q_nonnull']}/5  V:{stats['v_nonnull']}/3  M:{stats['m_nonnull']}/3  "
            f"Beneish:{beneish_flag:<20}{err_str}{sparse_warn}"
        )

    # ── Step 3: Composite scores ──────────────────────────────────────────────
    print("\n" + "=" * 72)
    print(f"STEP 3: COMPOSITE SCORES  (regime={args.regime})")
    print("=" * 72)

    scored_cands = [c for c in sample_cands if c.ticker in raw_factor_results]
    all_results = compute_composite(scored_cands, raw_factor_results, args.regime)
    all_results.sort(key=lambda r: r.composite_score, reverse=True)

    # Header
    print(
        f"\n{'Rk':<4} {'Ticker':<7} {'Sector':<14} "
        f"{'Comp':>5} {'Q':>5} {'V':>5} {'M':>5}  "
        f"{'MScore':>7}  {'Q/5':>3} {'V/3':>3} {'M/3':>3}  Note"
    )
    print("-" * 88)

    for r in all_results:
        stats = per_ticker_stats.get(r.ticker, {})
        q_data = f"{stats.get('q_nonnull', 0)}/5"
        v_data = f"{stats.get('v_nonnull', 0)}/3"
        m_data = f"{stats.get('m_nonnull', 0)}/3"
        total_nonnull = stats.get("q_nonnull", 0) + stats.get("v_nonnull", 0) + stats.get("m_nonnull", 0)

        note = ""
        if r.excluded:
            note = "[EXCLUDED]"
        elif r.composite_score >= 7.0:
            note = "** QUALIFIED **"
        if total_nonnull == 0:
            note += " [ALL-SPARSE]"
        elif total_nonnull <= 2:
            note += " [SPARSE]"

        mscore_str = f"{r.beneish_m_score:7.3f}" if r.beneish_m_score is not None else "   None"
        sector_str = (r.sector or "Unknown")[:13]

        print(
            f"{r.rank:<4} {r.ticker:<7} {sector_str:<14} "
            f"{r.composite_score:5.2f} {r.quality_score:5.2f} {r.value_score:5.2f} {r.momentum_score:5.2f}  "
            f"{mscore_str}  {q_data:>3} {v_data:>3} {m_data:>3}  {note}"
        )

    # ── Step 4: Diagnostic summary ────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("STEP 4: DIAGNOSTIC SUMMARY")
    print("=" * 72)

    non_excl = [r for r in all_results if not r.excluded]
    qualified = [r for r in non_excl if r.composite_score >= 7.0]
    excluded  = [r for r in all_results if r.excluded]

    print(f"\nResults overview:")
    print(f"  Scored:     {len(all_results)}")
    print(f"  Excluded:   {len(excluded)}  (Beneish hard gate)")
    print(f"  Eligible:   {len(non_excl)}")
    print(f"  Qualified:  {len(qualified)}  (composite ≥ 7.0)")

    # Score distribution
    composites = [r.composite_score for r in non_excl]
    if composites:
        composites_sorted = sorted(composites)
        mid = len(composites_sorted) // 2
        print(f"\nScore distribution (non-EXCLUDED):")
        print(f"  Min:    {composites_sorted[0]:.2f}")
        print(f"  Median: {composites_sorted[mid]:.2f}")
        print(f"  Max:    {composites_sorted[-1]:.2f}")
        buckets = [("0–3", 0, 3), ("3–5", 3, 5), ("5–7", 5, 7), ("7–10", 7, 10.01)]
        bucket_counts = {label: sum(1 for s in composites if lo <= s < hi) for label, lo, hi in buckets}
        max_bc = max(bucket_counts.values()) if bucket_counts else 1
        for label, count in bucket_counts.items():
            print(f"  [{label}]:  {count:3d}  {_bar(count, max_bc, 20)}")

    # Sparse detection
    print(f"\n[SPARSE CHECK] Factor data completeness:")
    sparse_all   = []
    sparse_heavy = []
    for r in non_excl:
        stats = per_ticker_stats.get(r.ticker, {})
        total = stats.get("q_nonnull", 0) + stats.get("v_nonnull", 0) + stats.get("m_nonnull", 0)
        if total == 0:
            sparse_all.append(r)
        elif total <= 2:
            sparse_heavy.append(r)

    if not sparse_all and not sparse_heavy:
        print(f"  [OK] All tickers have at least some real factor data")
    else:
        if sparse_all:
            print(f"  [WARN] {len(sparse_all)} tickers with ZERO non-null factor values:")
            for r in sparse_all:
                all_neutral = (
                    abs(r.quality_score - 5.0) < 0.1
                    and abs(r.value_score - 5.0) < 0.1
                    and abs(r.momentum_score - 5.0) < 0.1
                )
                silent = " → SILENT 5.0 (neutral default everywhere)" if all_neutral else ""
                print(f"    {r.ticker}: comp={r.composite_score:.2f} Q={r.quality_score:.2f} "
                      f"V={r.value_score:.2f} M={r.momentum_score:.2f}{silent}")
        if sparse_heavy:
            print(f"  [WARN] {len(sparse_heavy)} tickers with ≤2 non-null factor values (likely sparse):")
            for r in sparse_heavy:
                stats = per_ticker_stats[r.ticker]
                print(f"    {r.ticker}: comp={r.composite_score:.2f} "
                      f"Q:{stats['q_nonnull']}/5 V:{stats['v_nonnull']}/3 M:{stats['m_nonnull']}/3")

    # Silent-5.0 across all sub-scores check
    all_5_results = [
        r for r in non_excl
        if abs(r.quality_score - 5.0) < 0.05
        and abs(r.value_score - 5.0) < 0.05
        and abs(r.momentum_score - 5.0) < 0.05
    ]
    if all_5_results:
        print(f"\n[5.0 CHECK] {len(all_5_results)} tickers with Q≈V≈M≈5.0 (neutral defaults, likely sparse):")
        for r in all_5_results:
            stats = per_ticker_stats.get(r.ticker, {})
            total = stats.get("q_nonnull", 0) + stats.get("v_nonnull", 0) + stats.get("m_nonnull", 0)
            print(f"  [WARN] {r.ticker} (mktcap=${_fmt_opt(r.market_cap_m, '.0f')}M, {total}/11 fields computed)")
    else:
        print(f"\n[5.0 CHECK] [OK] No tickers with all-neutral (Q≈V≈M≈5.0) factor scores")

    # Qualified tickers detail
    if qualified:
        print(f"\n[QUALIFIED] Tickers scoring ≥ 7.0:")
        for r in sorted(qualified, key=lambda x: x.composite_score, reverse=True):
            stats = per_ticker_stats.get(r.ticker, {})
            total_fields = stats.get("q_nonnull", 0) + stats.get("v_nonnull", 0) + stats.get("m_nonnull", 0)
            rf = r.raw_factors
            q_rv = rf.get("quality", {})
            v_rv = rf.get("value", {})
            m_rv = rf.get("momentum", {})
            print(f"\n  {r.ticker} — composite={r.composite_score:.2f}  rank={r.rank}  sector={r.sector}")
            print(f"    Q={r.quality_score:.2f}  V={r.value_score:.2f}  M={r.momentum_score:.2f}  "
                  f"Beneish={r.beneish_flag} ({_fmt_opt(r.beneish_m_score)})  "
                  f"Insider={r.insider_signal}")
            print(f"    Market cap: ${_fmt_opt(r.market_cap_m, '.0f')}M   ADV: ${_fmt_opt(r.adv_k, '.0f')}K")
            print(f"    Data coverage: {total_fields}/11 factor fields computed")
            print(f"    Raw quality:  gross_margin={_fmt_opt(q_rv.get('gross_margin'))}  "
                  f"rev_growth={_fmt_opt(q_rv.get('revenue_growth_yoy'))}  "
                  f"roe={_fmt_opt(q_rv.get('roe'))}  "
                  f"d2e={_fmt_opt(q_rv.get('debt_to_equity'))}  "
                  f"eps_beat={_fmt_opt(q_rv.get('eps_beat_rate'))}")
            print(f"    Raw value:    ev_mult={_fmt_opt(v_rv.get('ev_multiple'))}  "
                  f"p_fcf={_fmt_opt(v_rv.get('p_fcf'))}  "
                  f"p_bk={_fmt_opt(v_rv.get('price_book'))}")
            print(f"    Raw momentum: 12-1={_fmt_opt(m_rv.get('price_12_1'))}  "
                  f"6-1={_fmt_opt(m_rv.get('price_6_1'))}  "
                  f"eps_rev={_fmt_opt(m_rv.get('eps_revision'))}")
            # Flag if qualified partly due to sparse data
            if total_fields <= 4:
                print(f"    [WARN] Only {total_fields}/11 fields computed — "
                      f"qualification may be driven by sparse-data 5.0 defaults")
    else:
        print(f"\n[QUALIFIED] No tickers scored ≥ 7.0 in this sample.")
        print(f"  (Normal if sample doesn't contain strong names; full universe run would find more.)")

    # Final summary line
    print("\n" + "=" * 72)
    print(f"DRY RUN COMPLETE — {len(scored_cands)} tickers scored, "
          f"{len(qualified)} qualified, {len(excluded)} excluded by Beneish")
    print(f"  To run full pipeline: python -c \"from backend.agents.screening_agent import run_screening; "
          f"r = run_screening(regime='{args.regime}'); print(len(r), 'qualified')\"")
    print("=" * 72 + "\n")


if __name__ == "__main__":
    main()
