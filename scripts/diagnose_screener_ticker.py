"""
Diagnose a single ticker through the screener's data pipeline.

Mirrors:
  fetch_ticker_data()      → raw_data (fmp + polygon_financials + price_history + yf_info)
  fetch_quality_fmp_batch  → fmp_quality
  score_quality / value / momentum / beneish

Then reports which raw sub-metrics came back None and probes FMP for replacements.
"""
import os
import sys
import json
from pathlib import Path

# Make backend importable when running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv()

import requests

from backend.screener.universe import fetch_ticker_data
from backend.fetchers.fmp_fetcher import fetch_quality_fmp_batch
from backend.screener.factors.quality import score_quality
from backend.screener.factors.value import score_value
from backend.screener.factors.momentum import score_momentum
from backend.financial_modeling.earnings_quality import compute_beneish


TICKER = sys.argv[1].upper() if len(sys.argv) > 1 else "BODI"
FMP_BASE = "https://financialmodelingprep.com/stable"
FMP_KEY = os.getenv("FMP_API_KEY")


def hr(title: str) -> None:
    print(f"\n{'='*72}\n{title}\n{'='*72}")


def jdump(label, obj, max_chars=600):
    s = json.dumps(obj, default=str, indent=2)
    if len(s) > max_chars:
        s = s[:max_chars] + f"... <truncated {len(s)-max_chars} chars>"
    print(f"{label}: {s}")


def probe_fmp(path: str, params: dict) -> tuple[int, object]:
    """Hit an FMP endpoint and return (status_code, body)."""
    if not FMP_KEY:
        return 0, "FMP_API_KEY missing"
    try:
        r = requests.get(f"{FMP_BASE}/{path}", params={**params, "apikey": FMP_KEY}, timeout=15)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, r.text[:500]
    except Exception as exc:
        return 0, f"error: {exc}"


def first(payload):
    if isinstance(payload, list) and payload:
        return payload[0]
    return None


# ── 1. Run screener-equivalent fetch ─────────────────────────────────────────
hr(f"STEP 1 — screener fetch_ticker_data({TICKER})")
raw = fetch_ticker_data(TICKER)
fmp_block = raw.get("fmp", {})
poly_results = raw.get("polygon_financials", {}).get("results", [])
yf_info = raw.get("yf_info", {})
price_hist = raw.get("price_history", [])

print(f"fmp keys: {len(fmp_block)}  | market_cap={fmp_block.get('market_cap')} "
      f"src={fmp_block.get('market_cap_source')}")
print(f"polygon_financials results: {len(poly_results)} "
      f"(periods: {[r.get('fiscal_period') for r in poly_results]})")
print(f"yf_info keys: {list(yf_info.keys())}")
print(f"price_history bars: {len(price_hist)}")

hr("STEP 2 — fetch_quality_fmp_batch")
fmp_quality_map = fetch_quality_fmp_batch([TICKER])
fmp_quality = fmp_quality_map.get(TICKER, {})
print(f"income_statement rows:        {len(fmp_quality.get('income_statement', []))}")
print(f"annual_income_statement rows: {len(fmp_quality.get('annual_income_statement', []))}")
print(f"balance_sheet rows:           {len(fmp_quality.get('balance_sheet', []))}")

# ── 3. Run factor scorers exactly like _score_ticker does ────────────────────
hr("STEP 3 — run factor scorers")
q = score_quality(TICKER, raw["polygon_financials"], yf_info, fmp_quality=fmp_quality)
v = score_value(TICKER, raw["polygon_financials"], fmp_block)
m = score_momentum(TICKER, price_hist, fmp_block)
try:
    b = compute_beneish(TICKER, raw["polygon_financials"])
except Exception as exc:
    b = {"error": str(exc)}

jdump("quality.raw_values",  q.get("raw_values", {}))
print(f"  pre_revenue_flag = {q.get('pre_revenue_flag')}   sector = {q.get('sector')}")
jdump("value.raw_values",    v.get("raw_values", {}))
jdump("momentum.raw_values", m.get("raw_values", {}))
print(f"  short_interest_bonus = {m.get('short_interest_bonus')}")
jdump("beneish", b)

# ── 4. Inventory of nulls ────────────────────────────────────────────────────
hr("STEP 4 — null inventory (data the screener is missing)")
missing: list[tuple[str, str]] = []
for sub, val in q.get("raw_values", {}).items():
    if val is None:
        missing.append(("quality",  sub))
for sub, val in v.get("raw_values", {}).items():
    if val is None and sub not in ("ev_type",):
        missing.append(("value",    sub))
for sub, val in m.get("raw_values", {}).items():
    if val is None:
        missing.append(("momentum", sub))

if not missing:
    print("nothing missing!")
else:
    for factor, sub in missing:
        print(f"  - {factor:9s} : {sub}")

# ── 5. Probe FMP for the gaps ────────────────────────────────────────────────
hr("STEP 5 — can FMP supply what's missing?")

# 5a. market_cap / EV inputs
hr("5a. market_cap, shares, price (drives EV / P/B / P/FCF)")
sc, body = probe_fmp(f"profile", {"symbol": TICKER})
print(f"  /profile  status={sc}")
prof = first(body)
if isinstance(prof, dict):
    for k in ("mktCap", "marketCap", "price", "beta", "sector", "industry"):
        if k in prof:
            print(f"    {k:12s} = {prof.get(k)}")

sc, body = probe_fmp(f"quote", {"symbol": TICKER})
print(f"  /quote    status={sc}")
qt = first(body)
if isinstance(qt, dict):
    for k in ("price", "marketCap", "sharesOutstanding", "eps", "pe"):
        if k in qt:
            print(f"    {k:18s} = {qt.get(k)}")

# 5b. cash flow statement for ttm OCF, capex, fcf_conversion
hr("5b. cash flow statement (OCF, capex → ttm OCF, FCF, FCF conversion)")
for period_label, params in [
    ("quarterly (TTM via sum)",  {"symbol": TICKER, "limit": 4}),
    ("annual",                   {"symbol": TICKER, "period": "annual", "limit": 2}),
]:
    sc, body = probe_fmp("cash-flow-statement", params)
    print(f"  /cash-flow-statement [{period_label}]  status={sc}  rows={len(body) if isinstance(body, list) else 'n/a'}")
    if isinstance(body, list):
        for row in body[:2]:
            if isinstance(row, dict):
                print(f"    date={row.get('date')}  "
                      f"OCF={row.get('operatingCashFlow') or row.get('netCashProvidedByOperatingActivities')}  "
                      f"capex={row.get('capitalExpenditure')}  "
                      f"FCF={row.get('freeCashFlow')}  "
                      f"NI={row.get('netIncome')}")

# 5c. balance sheet for book_value (equity), debt
hr("5c. balance sheet (equity = book_value, LT debt, cash)")
sc, body = probe_fmp("balance-sheet-statement", {"symbol": TICKER, "limit": 1})
print(f"  /balance-sheet-statement  status={sc}  rows={len(body) if isinstance(body, list) else 'n/a'}")
bs = first(body)
if isinstance(bs, dict):
    for k in ("date", "totalStockholdersEquity", "totalEquity",
              "longTermDebt", "cashAndCashEquivalents",
              "commonStockSharesOutstanding"):
        if k in bs:
            print(f"    {k:32s} = {bs.get(k)}")

# 5d. income statement for revenue, EBITDA, net income, EPS
hr("5d. income statement (revenue, EBITDA, net income, EPS history)")
sc, body = probe_fmp("income-statement", {"symbol": TICKER, "period": "annual", "limit": 2})
print(f"  /income-statement annual  status={sc}  rows={len(body) if isinstance(body, list) else 'n/a'}")
if isinstance(body, list):
    for row in body[:2]:
        if isinstance(row, dict):
            print(f"    date={row.get('date')}  rev={row.get('revenue')}  "
                  f"EBITDA={row.get('ebitda')}  NI={row.get('netIncome')}  "
                  f"EPS={row.get('eps')}")

# 5e. analyst estimates → eps_revision
hr("5e. analyst estimates over time (drives eps_revision)")
sc, body = probe_fmp("analyst-estimates", {"symbol": TICKER, "period": "annual", "limit": 4})
print(f"  /analyst-estimates  status={sc}  rows={len(body) if isinstance(body, list) else 'n/a'}")
if isinstance(body, list):
    for row in body[:3]:
        if isinstance(row, dict):
            print(f"    date={row.get('date')}  epsAvg={row.get('epsAvg') or row.get('estimatedEpsAvg')}  "
                  f"revAvg={row.get('revenueAvg') or row.get('estimatedRevenueAvg')}")

# 5f. earnings surprises → eps_beat_rate
hr("5f. earnings surprises (drives eps_beat_rate)")
sc, body = probe_fmp("earnings-surprises", {"symbol": TICKER})
print(f"  /earnings-surprises  status={sc}  rows={len(body) if isinstance(body, list) else 'n/a'}")
if isinstance(body, list):
    for row in body[:4]:
        if isinstance(row, dict):
            print(f"    date={row.get('date')}  est={row.get('estimatedEarning')}  "
                  f"actual={row.get('actualEarning')}")

# 5g. key metrics TTM — bundled multiples
hr("5g. key-metrics-ttm (bundled multiples: P/B, EV/EBITDA, P/FCF, ROIC)")
sc, body = probe_fmp("key-metrics-ttm", {"symbol": TICKER})
print(f"  /key-metrics-ttm  status={sc}  rows={len(body) if isinstance(body, list) else 'n/a'}")
km = first(body)
if isinstance(km, dict):
    for k in ("marketCapTTM", "enterpriseValueTTM", "evToEBITDATTM",
              "priceToBookTTM", "priceToFreeCashFlowsRatioTTM",
              "roicTTM", "debtToEquityTTM", "pegRatioTTM",
              "freeCashFlowPerShareTTM"):
        if k in km:
            print(f"    {k:32s} = {km.get(k)}")

# 5h. ratios TTM — alternative to key-metrics
hr("5h. ratios-ttm (alternative source for P/B, EV/EBITDA, D/E)")
sc, body = probe_fmp("ratios-ttm", {"symbol": TICKER})
print(f"  /ratios-ttm  status={sc}  rows={len(body) if isinstance(body, list) else 'n/a'}")
rt = first(body)
if isinstance(rt, dict):
    for k in ("priceToBookRatioTTM", "enterpriseValueMultipleTTM",
              "priceToFreeCashFlowsRatioTTM", "debtEquityRatioTTM",
              "grossProfitMarginTTM", "returnOnCapitalEmployedTTM"):
        if k in rt:
            print(f"    {k:32s} = {rt.get(k)}")

hr("DONE")
