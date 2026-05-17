"""
Market Intelligence Fetcher.

All market/profile/estimate/short-interest fields come from FMP. Polygon is
retained only for the deep financials shape (vX/reference/financials) that
downstream Beneish/value scorers key off.

yfinance has been removed from this module — it returned empty payloads
intermittently on micro-caps, silently dropping market_cap and cash and
masking otherwise-valid value scores. FMP covers every field yfinance was
previously providing.

Fields returned by fetch_fmp():
  - market_cap            FMP /profile → /quote → Polygon reference (final)
  - market_cap_source     "fmp_profile" | "fmp_quote" | "polygon_reference"
  - sector                FMP /profile
  - beta                  FMP /profile
  - cash                  FMP /balance-sheet-statement (most-recent quarter)
  - analyst_count         FMP /price-target-summary (lastQuarterCount)
  - target_mean_price     FMP /price-target-summary (lastQuarterAvgPriceTarget)
  - consensus_eps_*       FMP /analyst-estimates
  - consensus_revenue_*   FMP /analyst-estimates  (millions)
  - next_earnings_date    FMP /earnings (next upcoming with non-null epsEstimated)
  - short_interest_pct    Polygon /v3/reference/short-interest (optional)
  - days_to_cover         Polygon /v3/reference/short-interest (optional)
  - ev_ebitda_fmp         FMP /key-metrics-ttm evToEBITDATTM
  - price_book_fmp        FMP /key-metrics-ttm priceToBookTTM
  - ebitda_fmp            FMP /income-statement annual[0] ebitda
  - net_income, net_income_flag        Polygon income statement (validated)
  - long_term_debt, accounts_payable   Polygon balance sheet
  - interest_expense                   Polygon income statement
  - ttm_operating_cash_flow            Polygon cash flow (TTM or annualised)
  - revenue_recent, revenue_prior      Polygon FY income statement
  - gross_margin_pct                   Polygon FY income statement
  - polygon_financials_raw             Full Polygon /vX/reference/financials JSON

Quality data (FMP):
  fetch_quality_fmp_batch() fetches income statements + balance sheets for a
  list of tickers in async batches of 10 (6.0s inter-batch delay) and is used
  by the quality factor scorer to compute gross_margin, debt_to_equity, and
  revenue_growth_yoy with better small-cap coverage than Polygon.
"""

import asyncio
import os
import logging
from datetime import date

import httpx
import requests
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


# ── Helpers for fetch_fmp ─────────────────────────────────────────────────────

def _get_json(url: str, params: dict, timeout: int = 15) -> object | None:
    """GET an endpoint, parse JSON, return None on any failure. Never raises."""
    try:
        r = requests.get(url, params=params, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def _first(payload) -> dict | None:
    """Return the first dict of a list payload, or None."""
    if isinstance(payload, list) and payload:
        item = payload[0]
        if isinstance(item, dict):
            return item
    return None


def _fmp_profile(sym: str, fmp_key: str) -> dict | None:
    """FMP /profile — market_cap, sector, beta, price."""
    return _first(_get_json(f"{FMP_BASE}/profile", {"symbol": sym, "apikey": fmp_key}))


def _fmp_quote(sym: str, fmp_key: str) -> dict | None:
    """FMP /quote — market_cap fallback, sharesOutstanding."""
    return _first(_get_json(f"{FMP_BASE}/quote", {"symbol": sym, "apikey": fmp_key}))


def _fmp_price_target_summary(sym: str, fmp_key: str) -> dict | None:
    """FMP /price-target-summary — analyst_count, target_mean_price."""
    return _first(_get_json(f"{FMP_BASE}/price-target-summary", {"symbol": sym, "apikey": fmp_key}))


def _fmp_balance_sheet_latest(sym: str, fmp_key: str) -> dict | None:
    """FMP /balance-sheet-statement most recent quarter — cash."""
    return _first(_get_json(
        f"{FMP_BASE}/balance-sheet-statement",
        {"symbol": sym, "limit": 1, "apikey": fmp_key},
    ))


def _fmp_key_metrics_ttm(sym: str, fmp_key: str) -> dict | None:
    """FMP /key-metrics-ttm — evToEBITDATTM, priceToBookTTM, etc."""
    return _first(_get_json(
        f"{FMP_BASE}/key-metrics-ttm",
        {"symbol": sym, "apikey": fmp_key},
    ))


def _fmp_income_statement_annual(sym: str, fmp_key: str) -> list | None:
    """FMP /income-statement annual — ebitda, revenue, netIncome."""
    payload = _get_json(
        f"{FMP_BASE}/income-statement",
        {"symbol": sym, "period": "annual", "limit": 2, "apikey": fmp_key},
    )
    return payload if isinstance(payload, list) else None


def _fmp_next_earnings_date(sym: str, fmp_key: str) -> str | None:
    """
    FMP /earnings — find soonest future date with non-null epsEstimated.
    Returns YYYY-MM-DD or None.
    """
    payload = _get_json(
        f"{FMP_BASE}/earnings",
        {"symbol": sym, "limit": 4, "apikey": fmp_key},
    )
    if not isinstance(payload, list):
        return None
    today = date.today()
    candidates: list[str] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        d = row.get("date")
        eps_est = row.get("epsEstimated")
        if not d or eps_est is None:
            continue
        try:
            d_parsed = date.fromisoformat(str(d)[:10])
        except ValueError:
            continue
        if d_parsed >= today:
            candidates.append(d_parsed.isoformat())
    if not candidates:
        return None
    return min(candidates)


def _fmp_analyst_estimates(sym: str, fmp_key: str) -> list | None:
    """FMP /analyst-estimates annual — consensus EPS/revenue for current and next year."""
    payload = _get_json(
        f"{FMP_BASE}/analyst-estimates",
        {"symbol": sym, "period": "annual", "limit": 4, "apikey": fmp_key},
    )
    return payload if isinstance(payload, list) else None


def _polygon_short_interest(sym: str, polygon_key: str) -> tuple[float | None, float | None]:
    """
    Polygon /v3/reference/short-interest — best-effort fallback when FMP doesn't
    expose short interest. Returns (short_interest_pct, days_to_cover) or
    (None, None) when unavailable. Endpoint may not be on every Polygon tier.
    """
    try:
        r = requests.get(
            f"{POLYGON_BASE}/stocks/v1/short-interest/{sym}",
            params={"apiKey": polygon_key, "limit": 1},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            results = data.get("results") if isinstance(data, dict) else None
            if isinstance(results, list) and results:
                row = results[0]
                si = row.get("short_interest")
                avg_vol = row.get("avg_daily_volume")
                shares_out = row.get("shares_outstanding") or row.get("float")
                short_pct = None
                if si and shares_out:
                    try:
                        short_pct = round(float(si) / float(shares_out) * 100, 2)
                    except (ValueError, ZeroDivisionError):
                        pass
                dtc = None
                if si and avg_vol:
                    try:
                        dtc = round(float(si) / float(avg_vol), 2)
                    except (ValueError, ZeroDivisionError):
                        pass
                return short_pct, dtc
    except Exception:
        pass
    return None, None


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
        "market_cap_source": str | None,                 # "fmp_profile"|"fmp_quote"|"polygon_reference"
        "beta": float | None,
        "interest_expense": float | None,
        "ev_ebitda_fmp": float | None,
        "price_book_fmp": float | None,
        "ebitda_fmp": float | None,
        "polygon_financials_raw": dict | None,
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
        "revenue_recent": None,
        "revenue_prior": None,
        "gross_margin_pct": None,
        "next_earnings_date": None,
        "sector": None,
        "cash": None,
        "ttm_operating_cash_flow": None,
        "ocf_annualized": False,
        "cash_runway_months": None,
        "net_income": None,
        "net_income_flag": None,
        "long_term_debt": None,
        "accounts_payable": None,
        "market_cap": None,
        "market_cap_source": None,
        "beta": None,
        "interest_expense": None,
        "ev_ebitda_fmp": None,
        "price_book_fmp": None,
        "ebitda_fmp": None,
        "polygon_financials_raw": None,
        "error": None,
    }

    fmp_key = os.getenv("FMP_API_KEY")
    polygon_key = os.getenv("POLYGON_API_KEY")

    # ── FMP block (replaces yfinance entirely) ────────────────────────────────
    if fmp_key:
        # /profile — primary market_cap, sector, beta
        try:
            prof = _fmp_profile(sym, fmp_key)
            if prof:
                mc = prof.get("marketCap") or prof.get("mktCap")
                if mc:
                    try:
                        result["market_cap"] = float(mc)
                        result["market_cap_source"] = "fmp_profile"
                    except (TypeError, ValueError):
                        pass
                sector = prof.get("sector")
                if sector:
                    result["sector"] = sector
                beta_val = prof.get("beta")
                if beta_val is not None:
                    try:
                        result["beta"] = float(beta_val)
                    except (TypeError, ValueError):
                        pass
        except Exception as exc:
            logger.warning("fetch_fmp(%s): FMP /profile failed — %s", sym, exc)

        # /quote — market_cap fallback
        if result["market_cap"] is None:
            try:
                qt = _fmp_quote(sym, fmp_key)
                if qt:
                    mc = qt.get("marketCap")
                    if mc:
                        try:
                            result["market_cap"] = float(mc)
                            result["market_cap_source"] = "fmp_quote"
                        except (TypeError, ValueError):
                            pass
            except Exception as exc:
                logger.warning("fetch_fmp(%s): FMP /quote failed — %s", sym, exc)

        # /price-target-summary — analyst count + mean target
        try:
            pts = _fmp_price_target_summary(sym, fmp_key)
            if pts:
                ac = pts.get("lastQuarterCount") or pts.get("lastMonthCount")
                tmp = pts.get("lastQuarterAvgPriceTarget") or pts.get("lastMonthAvgPriceTarget")
                if ac is not None:
                    try:
                        result["analyst_count"] = int(ac)
                    except (TypeError, ValueError):
                        pass
                if tmp is not None:
                    try:
                        result["target_mean_price"] = float(tmp)
                    except (TypeError, ValueError):
                        pass
        except Exception as exc:
            logger.warning("fetch_fmp(%s): FMP /price-target-summary failed — %s", sym, exc)

        # /balance-sheet-statement — cash (latest period)
        try:
            bs_latest = _fmp_balance_sheet_latest(sym, fmp_key)
            if bs_latest:
                c = bs_latest.get("cashAndCashEquivalents")
                if c is not None:
                    try:
                        result["cash"] = float(c)
                    except (TypeError, ValueError):
                        pass
        except Exception as exc:
            logger.warning("fetch_fmp(%s): FMP /balance-sheet-statement failed — %s", sym, exc)

        # /earnings — next upcoming earnings date
        try:
            ned = _fmp_next_earnings_date(sym, fmp_key)
            if ned:
                result["next_earnings_date"] = ned
        except Exception as exc:
            logger.warning("fetch_fmp(%s): FMP /earnings failed — %s", sym, exc)

        # /analyst-estimates — forward EPS + revenue consensus
        try:
            estimates = _fmp_analyst_estimates(sym, fmp_key) or []
            current_year = date.today().year
            for est in estimates:
                if not isinstance(est, dict):
                    continue
                est_date = est.get("date", "")
                try:
                    est_year = int(str(est_date)[:4])
                except (ValueError, TypeError):
                    continue
                eps_avg = est.get("epsAvg") or est.get("estimatedEpsAvg")
                rev_avg = est.get("revenueAvg") or est.get("estimatedRevenueAvg")
                if est_year == current_year and result["consensus_eps_current_year"] is None:
                    if eps_avg is not None:
                        try:
                            result["consensus_eps_current_year"] = float(eps_avg)
                        except (TypeError, ValueError):
                            pass
                    if rev_avg is not None:
                        try:
                            result["consensus_revenue_current_year"] = round(float(rev_avg) / 1_000_000, 1)
                        except (TypeError, ValueError):
                            pass
                elif est_year == current_year + 1 and result["consensus_eps_next_year"] is None:
                    if eps_avg is not None:
                        try:
                            result["consensus_eps_next_year"] = float(eps_avg)
                        except (TypeError, ValueError):
                            pass
                    if rev_avg is not None:
                        try:
                            result["consensus_revenue_next_year"] = round(float(rev_avg) / 1_000_000, 1)
                        except (TypeError, ValueError):
                            pass
        except Exception as exc:
            logger.warning("fetch_fmp(%s): FMP /analyst-estimates failed — %s", sym, exc)

        # /key-metrics-ttm — bundled multiples for the value scorer
        try:
            km = _fmp_key_metrics_ttm(sym, fmp_key)
            if km:
                ev_eb = km.get("evToEBITDATTM")
                if ev_eb is not None:
                    try:
                        result["ev_ebitda_fmp"] = float(ev_eb)
                    except (TypeError, ValueError):
                        pass
                pb = km.get("priceToBookTTM")
                if pb is not None:
                    try:
                        result["price_book_fmp"] = float(pb)
                    except (TypeError, ValueError):
                        pass
        except Exception as exc:
            logger.warning("fetch_fmp(%s): FMP /key-metrics-ttm failed — %s", sym, exc)

        # /income-statement annual — EBITDA fallback for value scorer
        try:
            annual_inc = _fmp_income_statement_annual(sym, fmp_key)
            if annual_inc:
                first_row = annual_inc[0] if isinstance(annual_inc[0], dict) else None
                if first_row is not None:
                    eb = first_row.get("ebitda")
                    if eb is not None:
                        try:
                            result["ebitda_fmp"] = float(eb)
                        except (TypeError, ValueError):
                            pass
        except Exception as exc:
            logger.warning("fetch_fmp(%s): FMP /income-statement (annual) failed — %s", sym, exc)
    else:
        logger.warning("fetch_fmp(%s): FMP_API_KEY not set — FMP fields unavailable", sym)

    # ── Polygon short-interest (best-effort fallback for FMP gap) ─────────────
    # FMP Starter doesn't expose short interest; Polygon's reference endpoint
    # may not be on every tier either. If unavailable, leave both fields None
    # and the screener's short_interest_bonus simply won't apply.
    if polygon_key:
        try:
            si_pct, dtc = _polygon_short_interest(sym, polygon_key)
            if si_pct is not None:
                result["short_interest_pct"] = si_pct
            if dtc is not None:
                result["days_to_cover"] = dtc
        except Exception:
            pass

    # ── Polygon /v3/reference/tickers — market_cap final fallback ─────────────
    if polygon_key and result["market_cap"] is None:
        try:
            r = requests.get(
                f"{POLYGON_BASE}/v3/reference/tickers/{sym}",
                params={"apiKey": polygon_key},
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json().get("results", {})
                poly_mktcap = data.get("market_cap")
                if poly_mktcap is not None:
                    result["market_cap"] = poly_mktcap
                    result["market_cap_source"] = "polygon_reference"
        except Exception:
            pass

    # ── Polygon /vX/reference/financials — deep financials shape ──────────────
    # Load-bearing for Beneish M-score, value EV math, quality FCF conversion.
    if polygon_key:
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

                    # Revenue and gross margin from annual income statements
                    try:
                        fy_results = [r2 for r2 in results if r2.get("fiscal_period") == "FY"]
                        if fy_results:
                            inc_fy0 = fy_results[0].get("financials", {}).get("income_statement", {})
                            rev0 = inc_fy0.get("revenues", {}).get("value")
                            gp0 = inc_fy0.get("gross_profit", {}).get("value")
                            if rev0 is not None:
                                result["revenue_recent"] = float(rev0)
                            if rev0 and gp0 is not None:
                                result["gross_margin_pct"] = round(gp0 / rev0 * 100, 1)
                            if len(fy_results) >= 2:
                                inc_fy1 = fy_results[1].get("financials", {}).get("income_statement", {})
                                rev1 = inc_fy1.get("revenues", {}).get("value")
                                if rev1 is not None:
                                    result["revenue_prior"] = float(rev1)
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
