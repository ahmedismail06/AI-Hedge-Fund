"""
Test yfinance and Polygon as replacements for the deprecated FMP endpoints.

We need these fields (currently all null from FMP):
  - short_interest_pct     % of float short
  - days_to_cover          short interest / avg daily volume
  - analyst_count          number of analysts covering the stock
  - consensus_eps_current_year
  - consensus_eps_next_year
  - consensus_revenue_current_year  (in millions)
  - consensus_revenue_next_year     (in millions)
  - next_earnings_date

Run: python test_fmp_replacements.py
"""

import os
import json
import requests
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

TICKER = "PRCT"
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")


# ── yfinance ──────────────────────────────────────────────────────────────────

def test_yfinance(ticker: str):
    print("\n" + "=" * 60)
    print(f"  yfinance — {ticker}")
    print("=" * 60)

    t = yf.Ticker(ticker)

    # --- info dict (short interest, analyst count, etc.) ---
    info = t.info
    print("\n[info] relevant keys:")
    relevant = [
        "shortPercentOfFloat",   # % of float short (0–1 scale)
        "shortRatio",            # days to cover
        "numberOfAnalystOpinions",
        "targetMeanPrice",
        "recommendationKey",
    ]
    for k in relevant:
        print(f"  {k}: {info.get(k)}")

    # --- analyst estimates ---
    print("\n[analyst_price_targets]:")
    try:
        apt = t.analyst_price_targets
        print(apt)
    except Exception as e:
        print(f"  error: {e}")

    print("\n[earnings_estimate]:")
    try:
        ee = t.earnings_estimate
        print(ee)
    except Exception as e:
        print(f"  error: {e}")

    print("\n[revenue_estimate]:")
    try:
        re_ = t.revenue_estimate
        print(re_)
    except Exception as e:
        print(f"  error: {e}")

    # --- next earnings date ---
    print("\n[calendar]:")
    try:
        cal = t.calendar
        print(cal)
    except Exception as e:
        print(f"  error: {e}")


# ── Polygon ───────────────────────────────────────────────────────────────────

def test_polygon(ticker: str):
    print("\n" + "=" * 60)
    print(f"  Polygon — {ticker}")
    print("=" * 60)

    base = "https://api.polygon.io"
    key = POLYGON_API_KEY

    if not key:
        print("  POLYGON_API_KEY not set — skipping")
        return

    # --- Ticker details (analyst count, short interest not in free tier) ---
    print("\n[/v3/reference/tickers/{ticker}]:")
    r = requests.get(f"{base}/v3/reference/tickers/{ticker}", params={"apiKey": key}, timeout=15)
    print(f"  status: {r.status_code}")
    if r.status_code == 200:
        data = r.json().get("results", {})
        print(json.dumps({k: data.get(k) for k in [
            "name", "market_cap", "share_class_shares_outstanding",
            "weighted_shares_outstanding", "description"
        ]}, indent=4))
    else:
        print(f"  {r.text[:200]}")

    # --- Financials (income statement / balance sheet) ---
    print("\n[/vX/reference/financials]:")
    r = requests.get(f"{base}/vX/reference/financials", params={
        "ticker": ticker, "limit": 2, "apiKey": key
    }, timeout=15)
    print(f"  status: {r.status_code}")
    if r.status_code == 200:
        results = r.json().get("results", [])
        print(f"  {len(results)} filings returned")
        if results:
            first = results[0]
            print(f"  fiscal_period: {first.get('fiscal_period')} {first.get('fiscal_year')}")
            ic = first.get("financials", {}).get("income_statement", {})
            bs = first.get("financials", {}).get("balance_sheet", {})
            print(f"  revenues: {ic.get('revenues', {}).get('value')}")
            print(f"  long_term_debt: {bs.get('long_term_debt', {}).get('value')}")
            print(f"  accounts_payable: {bs.get('accounts_payable', {}).get('value')}")
    else:
        print(f"  {r.text[:200]}")

    # --- Snapshot (short interest not available in Polygon free tier) ---
    print("\n[/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}]:")
    r = requests.get(
        f"{base}/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}",
        params={"apiKey": key}, timeout=15
    )
    print(f"  status: {r.status_code}")
    if r.status_code == 200:
        snap = r.json().get("ticker", {})
        print(f"  day vol: {snap.get('day', {}).get('v')}")
        print(f"  prev close: {snap.get('prevDay', {}).get('c')}")
    else:
        print(f"  {r.text[:200]}")


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_yfinance(TICKER)
    test_polygon(TICKER)
