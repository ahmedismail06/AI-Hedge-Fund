"""
Value Factor Scorer (30% weight in composite).

Returns raw values for each sub-metric. Normalization is sector-relative
and deferred to scorer.py (value scores are normalized within SaaS /
Healthcare / Industrials cohorts, not universe-wide).

Sub-components:
  ev_multiple  40%  — EV/Revenue (pre-profit) or EV/EBITDA (profitable)
  p_fcf        30%  — Price/Free Cash Flow
  price_book   30%  — Price/Book

EV formula: EV = market_cap (FMP /profile) + LTD (Polygon) − cash (FMP balance sheet)

EBITDA precedence (most reliable first):
  1. FMP key-metrics-ttm.evToEBITDATTM (used directly as EV/EBITDA)
  2. FMP income-statement annual[0].ebitda (combined with our EV)
  3. Polygon FY EBITDA field
  4. Polygon operating_income (last-resort fallback; logs WARNING)
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

    # Warn when market_cap comes from a stale reference endpoint — EV multiples
    # may be directionally off if the price moved significantly since the filing.
    market_cap_source = fmp_data.get("market_cap_source")
    if market_cap_source == "polygon_reference":
        logger.debug(
            "%s: market_cap_source='polygon_reference' — EV multiples may use stale data",
            ticker,
        )

    # ── Enterprise Value ──────────────────────────────────────────────────────
    ev: Optional[float] = None
    if market_cap is not None:
        ev = market_cap + ltd - cash

    # ── EV Multiple (EV/Revenue for pre-profit, EV/EBITDA for profitable) ────
    # Source precedence:
    #   1. FMP key-metrics-ttm.evToEBITDATTM (passed in as ev_ebitda_fmp) — direct,
    #      uses FMP's own EV and true TTM EBITDA. Most accurate for small caps.
    #   2. FMP annual income-statement.ebitda (passed in as ebitda_fmp) — combined
    #      with our computed EV.
    #   3. Polygon FY ebitda field (often sparse for small caps).
    #   4. Polygon operating_income — last-resort fallback. Materially understates
    #      EBITDA (excludes D&A). Logs WARNING so we can audit how often it fires.
    ev_multiple: Optional[float] = None
    ev_type: Optional[str] = None
    is_profitable = False
    ebitda_used: Optional[float] = None
    ebitda_source: Optional[str] = None

    ev_ebitda_fmp = fmp_data.get("ev_ebitda_fmp")
    ebitda_fmp    = fmp_data.get("ebitda_fmp")
    polygon_ebitda = fin.get("ebitda")
    polygon_op_income = fin.get("operating_income")
    revenue = fin.get("revenue")

    # Pick EBITDA from best available source for the profitability flag
    if ebitda_fmp is not None:
        ebitda_used = ebitda_fmp
        ebitda_source = "fmp_income_statement"
    elif polygon_ebitda is not None:
        ebitda_used = polygon_ebitda
        ebitda_source = "polygon_fy"
    elif polygon_op_income is not None:
        ebitda_used = polygon_op_income
        ebitda_source = "polygon_operating_income_fallback"
        logger.warning(
            "%s: EBITDA unavailable from FMP/Polygon — falling back to Polygon operating_income (%.0f). "
            "EV/EBITDA multiple will be understated.",
            ticker, polygon_op_income,
        )

    if ebitda_used is not None and ebitda_used > 0:
        is_profitable = True

    # Apply the multiple in source-priority order
    if ev_ebitda_fmp is not None and ev_ebitda_fmp > 0:
        # FMP's own TTM EV/EBITDA — most reliable.
        ev_multiple = ev_ebitda_fmp
        ev_type = "EV/EBITDA"
        is_profitable = True
    elif ev is not None and is_profitable and ebitda_used and ebitda_used > 0:
        ev_multiple = ev / ebitda_used
        ev_type = "EV/EBITDA"
    elif ev is not None and revenue and revenue > 0:
        ev_multiple = ev / revenue
        ev_type = "EV/Revenue"

    # ── P/FCF ─────────────────────────────────────────────────────────────────
    p_fcf: Optional[float] = None
    ocf_annualized: bool = bool(fmp_data.get("ocf_annualized", False))
    if market_cap is not None and ttm_cfo is not None:
        capex = fin.get("capex")
        fcf = ttm_cfo - abs(capex) if capex is not None else ttm_cfo
        if fcf and fcf > 0:
            raw_p_fcf = market_cap / fcf
            if ocf_annualized:
                # Single-quarter annualisation overstates OCF for seasonal businesses.
                # Apply a 33% haircut (P/FCF 33% worse than naive) as a conservative
                # proxy for the distortion. Stored in raw_factors for audit visibility.
                p_fcf = raw_p_fcf * 1.33
                logger.debug(
                    "%s: ocf_annualized=True — p/fcf haircut applied: %.1f → %.1f",
                    ticker, raw_p_fcf, p_fcf,
                )
            else:
                p_fcf = raw_p_fcf

    # ── Price/Book ────────────────────────────────────────────────────────────
    # Prefer FMP key-metrics-ttm.priceToBookTTM (TTM, sector-comparable).
    price_book: Optional[float] = None
    price_book_fmp = fmp_data.get("price_book_fmp")
    book_value = fin.get("book_value")
    shares     = fin.get("shares")
    if price_book_fmp is not None and price_book_fmp > 0:
        price_book = price_book_fmp
    elif market_cap is not None and book_value is not None and book_value > 0:
        price_book = market_cap / book_value

    raw_values = {
        "ev_multiple":    ev_multiple,
        "p_fcf":          p_fcf,
        "price_book":     price_book,
        "ev_type":        ev_type,
        "ev":             ev,
        "is_profitable":  is_profitable,
        "ocf_annualized": ocf_annualized,  # True = single-quarter OCF; 33% P/FCF haircut applied
        "ebitda_source":  ebitda_source,   # source of EBITDA used for the multiple
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
