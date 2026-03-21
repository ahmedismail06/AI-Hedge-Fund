"""
Value Factor Scorer (30% weight in composite).

Returns raw values for each sub-metric. Normalization is sector-relative
and deferred to scorer.py (value scores are normalized within SaaS /
Healthcare / Industrials cohorts, not universe-wide).

Sub-components:
  ev_multiple  40%  — EV/Revenue (pre-profit) or EV/EBITDA (profitable)
  p_fcf        30%  — Price/Free Cash Flow
  price_book   30%  — Price/Book

EV formula: EV = market_cap (yfinance live) + LTD (Polygon) − cash (yfinance)
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _v(statement: dict, key: str) -> Optional[float]:
    val = statement.get(key, {})
    if isinstance(val, dict):
        return val.get("value")
    return val


def _get_latest_fy(polygon_financials: dict) -> dict:
    """Return the most recent FY row's flattened financials, or empty dict."""
    results = polygon_financials.get("results", [])
    fy_rows = [r for r in results if r.get("fiscal_period") == "FY"]
    fy_rows.sort(key=lambda r: r.get("filing_date", ""), reverse=True)
    if not fy_rows:
        return {}

    fin = fy_rows[0].get("financials", {})
    inc = fin.get("income_statement", {})
    bs  = fin.get("balance_sheet", {})
    cf  = fin.get("cash_flow_statement", {})
    return {
        "revenue":         _v(inc, "revenues"),
        "ebitda":          _v(inc, "earnings_before_interest_taxes_depreciation_and_amortization"),
        "operating_income":_v(inc, "operating_income_loss"),
        "capex":           _v(cf,  "capital_expenditure"),
        "cfo":             _v(cf,  "net_cash_flow_from_operating_activities"),
        "equity":          _v(bs,  "equity"),
        "book_value":      _v(bs,  "equity"),  # book value = stockholders' equity
        "shares":          _v(bs,  "common_shares_outstanding"),
    }


def score_value(ticker: str, polygon_financials: dict, fmp_data: dict) -> dict:
    """
    Compute raw value sub-metrics for a ticker.

    Args:
        ticker: Stock ticker symbol.
        polygon_financials: Raw Polygon /vX/reference/financials response.
        fmp_data: Output of fetch_fmp() — contains market_cap, long_term_debt, cash,
                  ttm_operating_cash_flow.

    Returns:
        {
            "ticker": str,
            "raw_values": {
                "ev_multiple": float | None,  # EV/Revenue or EV/EBITDA (lower = better)
                "p_fcf":       float | None,  # lower = better
                "price_book":  float | None,  # lower = better
                "ev_type":     "EV/Revenue" | "EV/EBITDA" | None,
                "ev":          float | None,
                "is_profitable": bool,
            },
        }
    All multiples: lower = better. Scorer.py inverts during normalization.
    """
    fin = _get_latest_fy(polygon_financials)

    market_cap = fmp_data.get("market_cap")
    ltd        = fmp_data.get("long_term_debt") or 0.0
    cash       = fmp_data.get("cash") or 0.0
    ttm_cfo    = fmp_data.get("ttm_operating_cash_flow")

    # ── Enterprise Value ──────────────────────────────────────────────────────
    ev: Optional[float] = None
    if market_cap is not None:
        ev = market_cap + ltd - cash

    # ── EV Multiple (EV/Revenue for pre-profit, EV/EBITDA for profitable) ────
    ev_multiple: Optional[float] = None
    ev_type: Optional[str] = None
    is_profitable = False

    ebitda = fin.get("ebitda") or fin.get("operating_income")
    revenue = fin.get("revenue")

    if ebitda is not None and ebitda > 0:
        is_profitable = True

    if ev is not None:
        if is_profitable and ebitda and ebitda > 0:
            ev_multiple = ev / ebitda
            ev_type = "EV/EBITDA"
        elif revenue and revenue > 0:
            ev_multiple = ev / revenue
            ev_type = "EV/Revenue"

    # ── P/FCF ─────────────────────────────────────────────────────────────────
    p_fcf: Optional[float] = None
    if market_cap is not None and ttm_cfo is not None:
        capex = fin.get("capex")
        fcf = ttm_cfo - abs(capex) if capex is not None else ttm_cfo
        if fcf and fcf > 0:
            p_fcf = market_cap / fcf

    # ── Price/Book ────────────────────────────────────────────────────────────
    price_book: Optional[float] = None
    book_value = fin.get("book_value")
    shares     = fin.get("shares")
    if market_cap is not None and book_value is not None and book_value > 0:
        price_book = market_cap / book_value

    raw_values = {
        "ev_multiple":   ev_multiple,
        "p_fcf":         p_fcf,
        "price_book":    price_book,
        "ev_type":       ev_type,
        "ev":            ev,
        "is_profitable": is_profitable,
    }

    logger.debug(
        "%s value raw: %s=%.1f p/fcf=%.1f p/b=%.2f",
        ticker,
        ev_type or "EV/n/a",
        ev_multiple or 0,
        p_fcf or 0,
        price_book or 0,
    )

    return {
        "ticker":     ticker.upper(),
        "raw_values": raw_values,
    }
