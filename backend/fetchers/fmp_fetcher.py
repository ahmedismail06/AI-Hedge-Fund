"""
Market Intelligence Fetcher
Replaces the deprecated FMP endpoints with yfinance (market data, estimates)
and Polygon (balance sheet fundamentals).

Fields returned:
  - short_interest_pct, days_to_cover       (yfinance)
  - analyst_count, target_mean_price        (yfinance)
  - consensus_eps_current_year/next_year    (yfinance)
  - consensus_revenue_current_year/next_year (yfinance, in millions)
  - next_earnings_date                      (yfinance)
  - cash                                    (yfinance quarterly balance sheet)
  - ttm_operating_cash_flow                 (Polygon cash flow statement)
  - cash_runway_months                      (computed: cash / monthly burn)
  - long_term_debt, accounts_payable        (Polygon /vX/reference/financials)
  - market_cap                              (Polygon /v3/reference/tickers)
"""

import os
from datetime import date

import requests
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

POLYGON_BASE = "https://api.polygon.io"


def fetch_fmp(ticker: str) -> dict:
    """
    Returns:
    {
        "ticker": str,
        "short_interest_pct": float | None,
        "days_to_cover": float | None,
        "analyst_count": int | None,
        "target_mean_price": float | None,
        "consensus_eps_current_year": float | None,
        "consensus_eps_next_year": float | None,
        "consensus_revenue_current_year": float | None,  # millions
        "consensus_revenue_next_year": float | None,     # millions
        "next_earnings_date": str | None,                # YYYY-MM-DD
        "long_term_debt": float | None,                  # raw dollars
        "accounts_payable": float | None,                # raw dollars
        "market_cap": float | None,                      # raw dollars
        "error": None | str,
    }
    Never raises — partial failures are silently skipped.
    """
    sym = ticker.upper()
    result: dict = {
        "ticker": sym,
        "short_interest_pct": None,
        "days_to_cover": None,
        "analyst_count": None,
        "target_mean_price": None,
        "consensus_eps_current_year": None,
        "consensus_eps_next_year": None,
        "consensus_revenue_current_year": None,
        "consensus_revenue_next_year": None,
        "next_earnings_date": None,
        "cash": None,
        "ttm_operating_cash_flow": None,
        "ocf_annualized": False,   # Bug 9: True when TTM OCF = single quarter × 4
        "cash_runway_months": None,
        "long_term_debt": None,
        "accounts_payable": None,
        "market_cap": None,
        "market_cap_source": None, # Bug 10: "yfinance" (live) or "polygon_reference" (stale)
        "error": None,
    }

    # ── yfinance ──────────────────────────────────────────────────────────────
    try:
        t = yf.Ticker(sym)
        info = t.info or {}

        # Short interest
        si = info.get("shortPercentOfFloat")
        if si is not None:
            result["short_interest_pct"] = round(si * 100, 2)  # convert 0–1 → %
        result["days_to_cover"] = info.get("shortRatio")
        result["analyst_count"] = info.get("numberOfAnalystOpinions")
        result["target_mean_price"] = info.get("targetMeanPrice")

        # Bug 10: yfinance marketCap is live (updated intraday); use as primary source.
        # Polygon /v3/reference/tickers returns a static reference field that can be
        # months stale. Valuation multiples computed against stale market cap are wrong.
        mktcap_yf = info.get("marketCap")
        if mktcap_yf:
            result["market_cap"] = float(mktcap_yf)
            result["market_cap_source"] = "yfinance"

        # Consensus EPS estimates
        try:
            ee = t.earnings_estimate
            if ee is not None and not ee.empty:
                # rows indexed by period: 0q, +1q, 0y, +1y
                if "0y" in ee.index:
                    result["consensus_eps_current_year"] = ee.loc["0y", "avg"] if "avg" in ee.columns else None
                if "+1y" in ee.index:
                    result["consensus_eps_next_year"] = ee.loc["+1y", "avg"] if "avg" in ee.columns else None
        except Exception:
            pass

        # Consensus revenue estimates
        try:
            re_ = t.revenue_estimate
            if re_ is not None and not re_.empty:
                if "0y" in re_.index:
                    val = re_.loc["0y", "avg"] if "avg" in re_.columns else None
                    if val is not None:
                        result["consensus_revenue_current_year"] = round(val / 1_000_000, 1)
                if "+1y" in re_.index:
                    val = re_.loc["+1y", "avg"] if "avg" in re_.columns else None
                    if val is not None:
                        result["consensus_revenue_next_year"] = round(val / 1_000_000, 1)
        except Exception:
            pass

        # Next earnings date
        try:
            cal = t.calendar or {}
            earnings_dates = cal.get("Earnings Date", [])
            today = date.today()
            for d in (earnings_dates if isinstance(earnings_dates, list) else [earnings_dates]):
                if hasattr(d, "date"):
                    d = d.date()
                if d >= today:
                    result["next_earnings_date"] = str(d)
                    break
        except Exception:
            pass

        # Cash from quarterly balance sheet
        try:
            qbs = t.quarterly_balance_sheet
            if qbs is not None and not qbs.empty:
                for row in ("Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"):
                    if row in qbs.index:
                        val = qbs.loc[row].iloc[0]
                        if val is not None and val == val:  # not NaN
                            result["cash"] = float(val)
                            break
        except Exception:
            pass

    except Exception as exc:
        result["error"] = f"yfinance error: {exc}"

    # ── Polygon balance sheet ─────────────────────────────────────────────────
    polygon_key = os.getenv("POLYGON_API_KEY")
    if polygon_key:
        # Market cap from Polygon — only used as fallback when yfinance didn't provide it.
        # Bug 10: this is reference data (static field), potentially months stale.
        # Prefer yfinance market cap (set above) for all valuation multiple calculations.
        try:
            r = requests.get(
                f"{POLYGON_BASE}/v3/reference/tickers/{sym}",
                params={"apiKey": polygon_key},
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json().get("results", {})
                poly_mktcap = data.get("market_cap")
                if poly_mktcap is not None and result["market_cap"] is None:
                    result["market_cap"] = poly_mktcap
                    result["market_cap_source"] = "polygon_reference"
        except Exception:
            pass

        # Balance sheet + cash flow from Polygon financials
        try:
            r = requests.get(
                f"{POLYGON_BASE}/vX/reference/financials",
                params={"ticker": sym, "limit": 2, "apiKey": polygon_key},
                timeout=15,
            )
            if r.status_code == 200:
                results = r.json().get("results", [])
                if results:
                    # Use most recent filing for balance sheet
                    bs = results[0].get("financials", {}).get("balance_sheet", {})
                    ltd = bs.get("long_term_debt", {}).get("value")
                    ap = bs.get("accounts_payable", {}).get("value")
                    if ltd is not None:
                        result["long_term_debt"] = ltd
                    if ap is not None:
                        result["accounts_payable"] = ap

                    # TTM operating cash flow — prefer TTM filing, else annualise Q4
                    ttm_ocf = None
                    for filing in results:
                        if filing.get("fiscal_period") == "TTM":
                            cf = filing.get("financials", {}).get("cash_flow_statement", {})
                            ttm_ocf = cf.get("net_cash_flow_from_operating_activities", {}).get("value")
                            break
                    if ttm_ocf is None:
                        # Fallback: annualise the most recent quarterly OCF.
                        # Bug 9: flag this approximation — Q4 is often the strongest quarter
                        # for seasonal businesses, so ×4 overstates annual OCF and
                        # understates (flatters) the computed cash runway.
                        cf = results[0].get("financials", {}).get("cash_flow_statement", {})
                        q_ocf = cf.get("net_cash_flow_from_operating_activities", {}).get("value")
                        if q_ocf is not None:
                            ttm_ocf = q_ocf * 4
                            result["ocf_annualized"] = True

                    if ttm_ocf is not None:
                        result["ttm_operating_cash_flow"] = ttm_ocf

                    # Compute runway: cash / monthly burn (only if burning cash)
                    cash = result.get("cash")
                    if cash and ttm_ocf is not None and ttm_ocf < 0:
                        monthly_burn = abs(ttm_ocf) / 12
                        result["cash_runway_months"] = round(cash / monthly_burn, 1)
        except Exception:
            pass

    return result
