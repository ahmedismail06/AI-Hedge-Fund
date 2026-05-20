"""
Quick scorer test — runs on MSFT, AAPL, GOOGL and prints composite scores.
Usage: python test_scorer.py
"""
import os
from dotenv import dotenv_values
os.environ.update(dotenv_values(".env"))

from backend.screener.universe import fetch_ticker_data, UniverseCandidate
from backend.screener.factors.quality import score_quality
from backend.screener.factors.value import score_value
from backend.screener.factors.momentum import score_momentum
from backend.screener.factors.earnings_quality import compute_beneish
from backend.screener.scorer import compute_composite

TICKERS = ["MSFT", "AAPL", "GOOGL"]

print("Fetching data (takes ~15s)...")
raw_map = {t: fetch_ticker_data(t) for t in TICKERS}

raw_factors = {}
for t in TICKERS:
    raw = raw_map[t]
    raw_factors[t] = {
        "quality":  score_quality(t, raw["polygon_financials"], raw["yf_info"]),
        "value":    score_value(t, raw["polygon_financials"], raw["fmp"]),
        "momentum": score_momentum(t, raw["price_history"], raw["fmp"]),
        "beneish":  compute_beneish(t, raw["polygon_financials"]),
        "fmp":      raw["fmp"],
        "form4":    {"insider_buy": False},
    }

universe = [UniverseCandidate(ticker=t, market_cap_m=500, sector="SaaS") for t in TICKERS]

for regime in ["Risk-On", "Risk-Off", "Stagflation"]:
    print(f"\n=== {regime} (Q/V/M weights: Risk-On=50/30/20, Risk-Off=60/30/10, Stagflation=55/35/10) ===")
    print(f"{'Ticker':<8} {'Composite':>9} {'Quality':>8} {'Value':>8} {'Momentum':>9}  {'Rank':>4}  Beneish")
    print("-" * 65)
    for r in compute_composite(universe, raw_factors, regime):
        print(f"{r.ticker:<8} {r.composite_score:>9.3f} {r.quality_score:>8.3f} {r.value_score:>8.3f} {r.momentum_score:>9.3f}  {r.rank:>4}  {r.beneish_flag}")
