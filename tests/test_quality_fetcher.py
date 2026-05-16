"""
Isolation test for quality factor fetcher improvements.

Usage:
    python test_quality_fetcher.py

Checks FMP gross_margin, debt_to_equity, and revenue_growth_yoy coverage on
5 representative tickers. REPL should come back as pre_revenue_flag=True.
"""

import json
from dotenv import load_dotenv

load_dotenv()

from backend.fetchers.fmp_fetcher import fetch_quality_fmp_batch
from backend.screener.factors.quality import score_quality

TICKERS = ["PCRX", "HRMY", "REPL", "PAR", "NRDS"]

print(f"\n{'='*60}")
print("Step 1: Fetching FMP quality data for all tickers ...")
print(f"{'='*60}")
fmp_map = fetch_quality_fmp_batch(TICKERS)

for ticker in TICKERS:
    data = fmp_map.get(ticker, {})
    inc_count  = len(data.get("income_statement", []))
    ann_count  = len(data.get("annual_income_statement", []))
    bs_count   = len(data.get("balance_sheet", []))
    print(f"  {ticker}: income_stmt={inc_count} periods  annual={ann_count}  balance_sheet={bs_count}")

print(f"\n{'='*60}")
print("Step 2: Running score_quality (Polygon data empty, FMP only) ...")
print(f"{'='*60}")

# Run quality scorer with empty Polygon data to isolate FMP path
empty_polygon = {"results": []}
empty_yf      = {}

results = {}
for ticker in TICKERS:
    result = score_quality(
        ticker,
        polygon_financials=empty_polygon,
        yf_info=empty_yf,
        fmp_quality=fmp_map.get(ticker),
    )
    results[ticker] = result

# Pretty-print results
for ticker, r in results.items():
    rv  = r["raw_values"]
    pre = r["pre_revenue_flag"]
    print(f"\n{ticker}  (pre_revenue={pre})")
    print(f"  gross_margin:        {rv['gross_margin']}")
    print(f"  revenue_growth_yoy:  {rv['revenue_growth_yoy']}")
    print(f"  roic:                {rv['roic']}")
    print(f"  fcf_conversion:      {rv['fcf_conversion']}")
    print(f"  debt_to_equity:      {rv['debt_to_equity']}")
    print(f"  eps_beat_rate:       {rv['eps_beat_rate']}")

print(f"\n{'='*60}")
print("Coverage summary")
print(f"{'='*60}")
METRICS = ("gross_margin", "revenue_growth_yoy", "debt_to_equity")
for metric in METRICS:
    populated = sum(1 for r in results.values() if r["raw_values"][metric] is not None)
    print(f"  {metric:<25} {populated}/{len(TICKERS)} populated")

print("\nExpected: PCRX, HRMY, PAR, NRDS → gross_margin + debt_to_equity populated")
print("Expected: REPL → pre_revenue_flag=True, gross_margin=None, revenue_growth_yoy=None")
