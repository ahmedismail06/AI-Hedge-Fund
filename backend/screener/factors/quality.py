"""
Quality Factor Scorer (50% weight in composite).

Returns raw values for each sub-metric. Normalization (0–10 percentile rank)
is deferred to scorer.py so rankings are relative across the full universe.

Sub-components:
  gross_margin       25%  — Polygon income_statement
  revenue_growth_yoy 20%  — Polygon income_statement (YoY)
  roe                20%  — Polygon: net_income / equity
  debt_to_equity     20%  — Polygon balance sheet (lower = better)
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


def score_quality(ticker: str, polygon_financials: dict, yf_info: dict) -> dict:
    """
    Compute raw quality sub-metrics for a ticker.

    Args:
        ticker: Stock ticker symbol.
        polygon_financials: Raw Polygon /vX/reference/financials response.
        yf_info: Raw yfinance Ticker.info dict (or empty dict on failure).

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
            "sector": str | None,   # passed through for sector-specific bonuses
        }
    Normalization is NOT done here — deferred to scorer.py.
    """
    cur, prior = _get_latest_two_fy(polygon_financials)

    # ── Gross Margin ──────────────────────────────────────────────────────────
    gross_margin: Optional[float] = None
    if cur.get("gross_profit") is not None and cur.get("revenue") and cur["revenue"] != 0:
        gross_margin = cur["gross_profit"] / cur["revenue"]
    elif cur.get("cogs") is not None and cur.get("revenue") and cur["revenue"] != 0:
        gross_margin = (cur["revenue"] - cur["cogs"]) / cur["revenue"]

    # ── Revenue Growth YoY ────────────────────────────────────────────────────
    revenue_growth_yoy: Optional[float] = None
    if cur.get("revenue") and prior.get("revenue") and prior["revenue"] != 0:
        revenue_growth_yoy = (cur["revenue"] - prior["revenue"]) / abs(prior["revenue"])

    # ── ROE ───────────────────────────────────────────────────────────────────
    roe: Optional[float] = None
    if cur.get("net_income") is not None and cur.get("equity") and cur["equity"] != 0:
        roe = cur["net_income"] / cur["equity"]

    # ── Debt-to-Equity (lower = better; will be inverted during normalization) ─
    debt_to_equity: Optional[float] = None
    ltd = cur.get("total_debt")
    eq  = cur.get("equity")
    if ltd is not None and eq and eq > 0:
        debt_to_equity = ltd / eq

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
        "%s quality raw: gm=%.2f rg=%.2f roe=%.2f d/e=%.2f beat=%.2f",
        ticker,
        gross_margin or 0,
        revenue_growth_yoy or 0,
        roe or 0,
        debt_to_equity or 0,
        eps_beat_rate or 0,
    )

    return {
        "ticker":     ticker.upper(),
        "raw_values": raw_values,
        "sector":     yf_info.get("sector"),
    }
