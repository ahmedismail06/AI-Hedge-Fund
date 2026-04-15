"""
Quality Factor Scorer (50% weight in composite).

Returns raw values for each sub-metric. Normalization (0–10 percentile rank)
is deferred to scorer.py so rankings are relative across the full universe.

Sub-components:
  gross_margin       25%  — FMP income-statement (primary), Polygon fallback
  revenue_growth_yoy 20%  — Polygon FY (primary), FMP annual fallback
  roe                20%  — Polygon: net_income / equity
  debt_to_equity     20%  — FMP balance-sheet-statement (lower = better)
  eps_beat_rate      15%  — yfinance earningsHistory

Sector-specific bonuses are applied in scorer.py after normalization.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _v(statement: dict, key: str) -> Optional[float]:
    """Extract .value from a Polygon financial statement field."""
    val = statement.get(key, {})
    if isinstance(val, dict):
        return val.get("value")
    return val


def _get_latest_two_fy(polygon_financials: dict) -> tuple[dict, dict]:
    """
    Return (current_FY_fields, prior_FY_fields) from Polygon financials.
    Returns empty dicts if fewer than 2 FY rows.
    """
    results = polygon_financials.get("results", [])
    fy_rows = [r for r in results if r.get("fiscal_period") == "FY"]
    fy_rows.sort(key=lambda r: r.get("filing_date", ""), reverse=True)
    if len(fy_rows) < 2:
        return {}, {}

    def extract(row: dict) -> dict:
        fin = row.get("financials", {})
        inc = fin.get("income_statement", {})
        bs  = fin.get("balance_sheet", {})
        return {
            "revenue":      _v(inc, "revenues"),
            "gross_profit": _v(inc, "gross_profit"),
            "cogs":         _v(inc, "cost_of_revenue"),
            "net_income":   _v(inc, "net_income_loss"),
            "equity":       _v(bs,  "equity"),
            "total_debt":   _v(bs,  "long_term_debt"),
            "current_debt": _v(bs,  "current_portion_of_long_term_debt"),
        }

    return extract(fy_rows[0]), extract(fy_rows[1])


def _eps_beat_rate(yf_info: dict) -> Optional[float]:
    """
    Estimate EPS beat rate from yfinance earningsHistory.
    yf_info["earningsHistory"] is a list of quarterly dicts with keys
    'epsEstimate' and 'epsActual'.
    Returns fraction of quarters where actual > estimate (0.0–1.0), or None.
    """
    history = yf_info.get("earningsHistory", [])
    if not history:
        return None
    beats = sum(
        1 for q in history
        if q.get("epsActual") is not None
        and q.get("epsEstimate") is not None
        and q["epsActual"] > q["epsEstimate"]
    )
    return beats / len(history) if history else None


# ── FMP sub-metric helpers ────────────────────────────────────────────────────

def _fmp_gross_margin(income_stmts: list[dict]) -> tuple[Optional[float], bool]:
    """
    Compute gross margin from FMP quarterly income statements.

    Returns (gross_margin, pre_revenue_flag).
    - gross_margin: grossProfit/revenue if available, else (revenue-cost)/revenue
    - pre_revenue_flag: True when all periods report revenue == 0 or null
    """
    if not income_stmts:
        return None, False

    # Try most recent period with both grossProfit and revenue
    for stmt in income_stmts:
        rev = stmt.get("revenue")
        gp  = stmt.get("grossProfit")
        if rev and rev != 0 and gp is not None:
            return gp / rev, False

    # Try cost-of-revenue variants
    _COST_FIELDS = ("costOfRevenue", "costOfGoodsSold", "costOfServices")
    for stmt in income_stmts:
        rev = stmt.get("revenue")
        if not rev or rev == 0:
            continue
        for field in _COST_FIELDS:
            cost = stmt.get(field)
            if cost is not None:
                return (rev - cost) / rev, False

    # Check if company is pre-revenue
    all_rev = [s.get("revenue") for s in income_stmts]
    if all(r is None or r == 0 for r in all_rev):
        return None, True

    return None, False


def _fmp_debt_to_equity(balance_sheets: list[dict]) -> Optional[float]:
    """
    Compute debt-to-equity from FMP balance sheet statements.

    - Negative equity (distressed) → returns 10.0 as a penalty value
    - Both fields missing → returns None
    """
    for bs in balance_sheets:
        total_debt = bs.get("totalDebt")
        equity     = bs.get("totalStockholdersEquity")
        if total_debt is None or equity is None:
            continue
        if equity == 0:
            continue
        if equity < 0:
            return 10.0  # distressed; scorer inverts D/E so high = bad
        return total_debt / equity
    return None


def _fmp_revenue_growth_annual(annual_stmts: list[dict]) -> Optional[float]:
    """
    Compute YoY revenue growth from FMP annual income statements.
    Requires at least 2 annual periods.
    """
    if len(annual_stmts) < 2:
        return None
    rev1 = annual_stmts[0].get("revenue")
    rev2 = annual_stmts[1].get("revenue")
    if rev1 is None or rev2 is None or rev2 == 0:
        return None
    return (rev1 - rev2) / abs(rev2)


# ── Main scorer ───────────────────────────────────────────────────────────────

def score_quality(
    ticker: str,
    polygon_financials: dict,
    yf_info: dict,
    fmp_quality: Optional[dict] = None,
) -> dict:
    """
    Compute raw quality sub-metrics for a ticker.

    Args:
        ticker:              Stock ticker symbol.
        polygon_financials:  Raw Polygon /vX/reference/financials response.
        yf_info:             Raw yfinance Ticker.info dict (or empty dict on failure).
        fmp_quality:         Pre-fetched FMP data dict:
                               {"income_statement": [...],
                                "annual_income_statement": [...],
                                "balance_sheet": [...]}
                             Pass None (or omit) to skip FMP sources.

    Returns:
        {
            "ticker": str,
            "raw_values": {
                "gross_margin":        float | None,   # 0–1 (e.g. 0.65)
                "revenue_growth_yoy":  float | None,   # e.g. 0.15 = 15% growth
                "roe":                 float | None,   # e.g. 0.18
                "debt_to_equity":      float | None,   # lower = better
                "eps_beat_rate":       float | None,   # 0–1
            },
            "pre_revenue_flag": bool,  # True if company has no revenue
            "sector": str | None,
        }
    Normalization is NOT done here — deferred to scorer.py.
    """
    cur, prior = _get_latest_two_fy(polygon_financials)

    fmp_inc    = (fmp_quality or {}).get("income_statement", [])
    fmp_annual = (fmp_quality or {}).get("annual_income_statement", [])
    fmp_bs     = (fmp_quality or {}).get("balance_sheet", [])

    # ── Gross Margin ──────────────────────────────────────────────────────────
    # Primary: FMP quarterly income statement (better small-cap coverage)
    gross_margin, pre_revenue = _fmp_gross_margin(fmp_inc)

    # Fallback: Polygon FY data (only if FMP gave nothing and not pre-revenue)
    if gross_margin is None and not pre_revenue:
        if cur.get("gross_profit") is not None and cur.get("revenue") and cur["revenue"] != 0:
            gross_margin = cur["gross_profit"] / cur["revenue"]
        elif cur.get("cogs") is not None and cur.get("revenue") and cur["revenue"] != 0:
            gross_margin = (cur["revenue"] - cur["cogs"]) / cur["revenue"]

    # ── Revenue Growth YoY ────────────────────────────────────────────────────
    # Primary: Polygon FY (most complete for YoY)
    revenue_growth_yoy: Optional[float] = None
    if cur.get("revenue") and prior.get("revenue") and prior["revenue"] != 0:
        revenue_growth_yoy = (cur["revenue"] - prior["revenue"]) / abs(prior["revenue"])

    # Fallback: FMP annual income statement
    if revenue_growth_yoy is None:
        revenue_growth_yoy = _fmp_revenue_growth_annual(fmp_annual)

    # ── ROE ───────────────────────────────────────────────────────────────────
    roe: Optional[float] = None
    if cur.get("net_income") is not None and cur.get("equity") and cur["equity"] != 0:
        roe = cur["net_income"] / cur["equity"]

    # ── Debt-to-Equity ────────────────────────────────────────────────────────
    # Primary: FMP balance sheet (pre-computed ratio fields missing for most names)
    # Polygon fallback removed — Polygon's pre-computed D/E field has 19% coverage
    debt_to_equity: Optional[float] = _fmp_debt_to_equity(fmp_bs)

    # ── EPS Beat Rate ─────────────────────────────────────────────────────────
    eps_beat_rate = _eps_beat_rate(yf_info)

    raw_values = {
        "gross_margin":       gross_margin,
        "revenue_growth_yoy": revenue_growth_yoy,
        "roe":                roe,
        "debt_to_equity":     debt_to_equity,
        "eps_beat_rate":      eps_beat_rate,
    }

    logger.debug(
        "%s quality raw: gm=%s rg=%s roe=%s d/e=%s beat=%s pre_rev=%s",
        ticker,
        f"{gross_margin:.3f}" if gross_margin is not None else "None",
        f"{revenue_growth_yoy:.3f}" if revenue_growth_yoy is not None else "None",
        f"{roe:.3f}" if roe is not None else "None",
        f"{debt_to_equity:.3f}" if debt_to_equity is not None else "None",
        f"{eps_beat_rate:.3f}" if eps_beat_rate is not None else "None",
        pre_revenue,
    )

    return {
        "ticker":           ticker.upper(),
        "raw_values":       raw_values,
        "pre_revenue_flag": pre_revenue,
        "sector":           yf_info.get("sector"),
    }
