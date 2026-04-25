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
  - sector                                  (yfinance)
  - cash                                    (yfinance quarterly balance sheet)
  - ttm_operating_cash_flow                 (Polygon cash flow statement)
  - cash_runway_months                      (computed: cash / monthly burn)
  - net_income, net_income_flag             (Polygon income statement, validation flag)
  - long_term_debt, accounts_payable        (Polygon /vX/reference/financials)
  - market_cap                              (Polygon /v3/reference/tickers)

Quality data (FMP):
  fetch_quality_fmp_batch() fetches income statements + balance sheets for a
  list of tickers in async batches of 50 (0.5s inter-batch delay) and is used
  by the quality factor scorer to compute gross_margin, debt_to_equity, and
  revenue_growth_yoy with better small-cap coverage than Polygon.
"""

import asyncio
import os
import logging
from datetime import date

import httpx
import requests
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

POLYGON_BASE = "https://api.polygon.io"
FMP_BASE = "https://financialmodelingprep.com/stable"
FMP_BASE_V3 = "https://financialmodelingprep.com/api/v3"
logger = logging.getLogger(__name__)

# ── FMP Quality Data Batch Fetch ──────────────────────────────────────────────

_EMPTY_QUALITY: dict = {
    "income_statement": [],
    "annual_income_statement": [],
    "balance_sheet": [],
}


async def _fetch_ticker_quality_async(
    client: httpx.AsyncClient,
    ticker: str,
    fmp_key: str,
) -> dict:
    """
    Fetch FMP income statements (quarterly + annual) and balance sheet for one ticker.
    Returns dict with keys: income_statement, annual_income_statement, balance_sheet.
    Never raises — returns empty lists on any error.
    """
    result = {
        "income_statement": [],
        "annual_income_statement": [],
        "balance_sheet": [],
    }
    sym = ticker.upper()

    async def safe_get(url: str, params: dict) -> list:
        try:
            r = await client.get(url, params=params, timeout=15.0)
            if r.status_code == 200:
                data = r.json()
                return data if isinstance(data, list) else []
        except Exception:
            pass
        return []

    result["income_statement"] = await safe_get(
        f"{FMP_BASE}/income-statement",
        {"symbol": sym, "limit": 4, "apikey": fmp_key},
    )
    result["annual_income_statement"] = await safe_get(
        f"{FMP_BASE}/income-statement",
        {"symbol": sym, "period": "annual", "limit": 2, "apikey": fmp_key},
    )
    result["balance_sheet"] = await safe_get(
        f"{FMP_BASE}/balance-sheet-statement",
        {"symbol": sym, "limit": 4, "apikey": fmp_key},
    )
    return result


async def _quality_batch_async(tickers: list[str], fmp_key: str) -> dict[str, dict]:
    """
    Fetch FMP quality data for all tickers in batches of 10 with a 6.0s inter-batch
    delay to perfectly coast under the 300 req/min FMP Starter rate limit.
    """
    results: dict[str, dict] = {}
    batch_size = 10  # Reduced from 50

    async with httpx.AsyncClient() as client:
        for start in range(0, len(tickers), batch_size):
            batch = tickers[start: start + batch_size]
            tasks = [_fetch_ticker_quality_async(client, t, fmp_key) for t in batch]
            batch_results = await asyncio.gather(*tasks)
            for ticker, data in zip(batch, batch_results):
                results[ticker] = data
            if start + batch_size < len(tickers):
                await asyncio.sleep(6.0)  # Increased from 0.5s

    return results


def fetch_quality_fmp_batch(tickers: list[str]) -> dict[str, dict]:
    """
    Sync entry point: batch-fetch FMP income statement + balance sheet for
    quality factor scoring (gross_margin, debt_to_equity, revenue_growth_yoy).

    Returns:
        {ticker: {"income_statement": [...], "annual_income_statement": [...],
                  "balance_sheet": [...]}}
    Falls back to empty lists per ticker on missing API key or fatal error.
    """
    fmp_key = os.getenv("FMP_API_KEY")
    if not fmp_key:
        logger.warning("FMP_API_KEY not set — quality FMP data unavailable")
        return {t: dict(_EMPTY_QUALITY) for t in tickers}

    try:
        return asyncio.run(_quality_batch_async(tickers, fmp_key))
    except RuntimeError:
        # asyncio.run() fails if there's already a running loop (e.g. Jupyter).
        # Fall back to a new thread-based loop.
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                lambda: asyncio.run(_quality_batch_async(tickers, fmp_key))
            )
            try:
                return future.result(timeout=600)
            except Exception as exc:
                logger.error("FMP quality batch fetch failed: %s", exc)
                return {t: dict(_EMPTY_QUALITY) for t in tickers}
    except Exception as exc:
        logger.error("FMP quality batch fetch failed: %s", exc)
        return {t: dict(_EMPTY_QUALITY) for t in tickers}


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
        "sector": str | None,
        "cash": float | None,
        "ttm_operating_cash_flow": float | None,
        "cash_runway_months": float | None,
        "net_income": float | None,
        "net_income_flag": str | None,
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
        "sector": None,
        "cash": None,
        "ttm_operating_cash_flow": None,
        "ocf_annualized": False,   # Bug 9: True when TTM OCF = single quarter × 4
        "cash_runway_months": None,
        "net_income": None,        # raw dollars (Polygon income statement)
        "net_income_flag": None,   # e.g., "SUSPECT_NET_INCOME"
        "long_term_debt": None,
        "accounts_payable": None,
        "market_cap": None,
        "market_cap_source": None, # Bug 10: "yfinance" (live) or "polygon_reference" (stale)
        "beta": None,
        "interest_expense": None,
        "polygon_financials_raw": None,
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
        result["sector"] = info.get("sector")

        # Beta
        try:
            beta_val = info.get("beta")
            if beta_val is not None:
                result["beta"] = float(beta_val)
        except Exception:
            pass

        # Bug 10: yfinance marketCap is live (updated intraday); use as primary source.
        # Polygon /v3/reference/tickers returns a static reference field that can be
        # months stale. Valuation multiples computed against stale market cap are wrong.
        mktcap_yf = info.get("marketCap")
        if mktcap_yf:
            result["market_cap"] = float(mktcap_yf)
            result["market_cap_source"] = "yfinance"

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

    fmp_key = os.getenv("FMP_API_KEY")
    if fmp_key:
        try:
            r = requests.get(
                f"{FMP_BASE}/analyst-estimates",
                params={"symbol": sym, "period": "annual", "limit": 2, "apikey": fmp_key},
                timeout=15,
            )
            if r.status_code == 200:
                estimates = r.json() or []
                current_year = date.today().year
                for est in estimates:
                    est_date = est.get("date", "")
                    try:
                        est_year = int(est_date[:4])
                    except (ValueError, TypeError):
                        continue
                    eps_avg = est.get("epsAvg")
                    rev_avg = est.get("revenueAvg")
                    if est_year == current_year and result["consensus_eps_current_year"] is None:
                        if eps_avg is not None:
                            result["consensus_eps_current_year"] = float(eps_avg)
                        if rev_avg is not None:
                            result["consensus_revenue_current_year"] = round(float(rev_avg) / 1_000_000, 1)
                    elif est_year == current_year + 1 and result["consensus_eps_next_year"] is None:
                        if eps_avg is not None:
                            result["consensus_eps_next_year"] = float(eps_avg)
                        if rev_avg is not None:
                            result["consensus_revenue_next_year"] = round(float(rev_avg) / 1_000_000, 1)
        except Exception as exc:
            logger.warning("fetch_fmp(%s): FMP analyst-estimates failed — %s", sym, exc)

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

        # Balance sheet + cash flow from Polygon financials.
        # limit=16 ensures 2 FY periods appear for Beneish (quarterly filings would
        # fill limit=3 leaving zero or one annual row, breaking the M-score calc).
        try:
            r = requests.get(
                f"{POLYGON_BASE}/vX/reference/financials",
                params={"ticker": sym, "limit": 16, "apiKey": polygon_key},
                timeout=15,
            )
            if r.status_code == 200:
                raw_json = r.json()
                result["polygon_financials_raw"] = raw_json
                results = raw_json.get("results", [])
                if results:
                    # Use most recent filing for balance sheet
                    bs = results[0].get("financials", {}).get("balance_sheet", {})
                    ltd = bs.get("long_term_debt", {}).get("value")
                    ap = bs.get("accounts_payable", {}).get("value")
                    if ltd is not None:
                        result["long_term_debt"] = ltd
                    if ap is not None:
                        result["accounts_payable"] = ap

                    # Interest expense from FY[0] income statement
                    try:
                        fy_results = [r2 for r2 in results if r2.get("fiscal_period") == "FY"]
                        if fy_results:
                            inc0 = fy_results[0].get("financials", {}).get("income_statement", {})
                            ie = (
                                inc0.get("interest_expense_operating", {}).get("value")
                                or inc0.get("interest_expense", {}).get("value")
                            )
                            if ie is not None:
                                result["interest_expense"] = abs(float(ie))
                    except Exception:
                        pass

                    # Net income — prefer TTM filing if available
                    net_income = None
                    for filing in results:
                        if filing.get("fiscal_period") == "TTM":
                            inc = filing.get("financials", {}).get("income_statement", {})
                            net_income = (
                                inc.get("net_income_loss", {}).get("value")
                                or inc.get("net_income", {}).get("value")
                            )
                            break
                    if net_income is None:
                        inc = results[0].get("financials", {}).get("income_statement", {})
                        net_income = (
                            inc.get("net_income_loss", {}).get("value")
                            or inc.get("net_income", {}).get("value")
                        )
                    if net_income is not None:
                        result["net_income"] = net_income

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

                    # Validate net income vs. cash burn — flag suspect values without correcting
                    if ttm_ocf is not None and ttm_ocf < 0 and result["net_income"] is not None:
                        ttm_outflow = abs(ttm_ocf)
                        if abs(result["net_income"]) < (ttm_outflow * 0.10):
                            result["net_income_flag"] = "SUSPECT_NET_INCOME"
                            logger.warning(
                                "fetch_fmp(%s): SUSPECT_NET_INCOME — net_income=%s vs TTM OCF=%s; suppressing net_income",
                                sym,
                                result["net_income"],
                                ttm_ocf,
                            )
                            result["net_income"] = None

                    # Compute runway: cash / monthly burn (only if burning cash)
                    cash = result.get("cash")
                    if cash and ttm_ocf is not None and ttm_ocf < 0:
                        monthly_burn = abs(ttm_ocf) / 12
                        result["cash_runway_months"] = round(cash / monthly_burn, 1)
        except Exception:
            pass

    return result
