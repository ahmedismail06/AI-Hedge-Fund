"""
Earnings Quality Gate — Beneish M-Score.

Pre-score gate applied before any factor weighting.
Uses two consecutive annual 10-K periods from Polygon /vX/reference/financials.

Thresholds:
  M > −1.78  → EXCLUDED  (hard gate: removed from universe before composite scoring)
  M > −2.22  → FLAGGED   (−0.5 penalty applied in scorer.py)
  M ≤ −2.22  → CLEAN
  < 2 FY periods → INSUFFICIENT_DATA (proceed, no penalty)
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Beneish (1999) 8-variable model coefficients
_INTERCEPT = -4.84
_COEFF = {
    "DSRI":  0.920,
    "GMI":   0.528,
    "AQI":   0.404,
    "SGI":   0.892,
    "DEPI":  0.115,
    "SGAI": -0.172,
    "TATA":  4.679,
    "LVGI": -0.327,
}


def _safe_div(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    """Returns numerator/denominator, or None if either is None or denominator is 0."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _extract_fy_periods(polygon_financials: dict) -> list[dict]:
    """
    Extract the two most recent FY periods from Polygon financials results.
    polygon_financials is the raw dict returned by fetch_ticker_data(), keyed as:
        {"results": [...], ...}
    Returns list of up to 2 dicts, each with flattened financial fields, sorted newest first.
    """
    results = polygon_financials.get("results", [])
    fy_rows = [r for r in results if r.get("fiscal_period") == "FY"]
    # Sort by filing_date descending; Polygon returns newest first but be explicit
    fy_rows.sort(key=lambda r: r.get("filing_date", ""), reverse=True)
    return fy_rows[:2]


def _extract_fields(row: dict) -> dict:
    """Flatten the financial statement fields from a single Polygon FY row."""
    fin = row.get("financials", {})
    inc = fin.get("income_statement", {})
    bs  = fin.get("balance_sheet", {})
    cf  = fin.get("cash_flow_statement", {})

    def v(statement: dict, key: str) -> Optional[float]:
        val = statement.get(key, {})
        if isinstance(val, dict):
            return val.get("value")
        return val

    return {
        "revenue":           v(inc, "revenues"),
        "cogs":              v(inc, "cost_of_revenue"),
        "gross_profit":      v(inc, "gross_profit"),
        "sga":               v(inc, "selling_general_administrative_expenses"),
        "depreciation":      v(inc, "depreciation_and_amortization"),
        "net_income":        v(inc, "net_income_loss"),
        "total_assets":      v(bs,  "assets"),
        "current_assets":    v(bs,  "current_assets"),
        "ppe_net":           v(bs,  "fixed_assets"),
        "receivables":       v(bs,  "accounts_receivable"),
        "ltd":               v(bs,  "long_term_debt"),
        "current_liabilities": v(bs, "current_liabilities"),
        "cfo":               v(cf,  "net_cash_flow_from_operating_activities"),
    }


def compute_beneish(ticker: str, polygon_financials: dict) -> dict:
    """
    Compute the Beneish M-Score for a ticker using Polygon annual financial data.

    Args:
        ticker: Stock ticker symbol.
        polygon_financials: Raw Polygon /vX/reference/financials response dict
                            (as returned by fetch_ticker_data()["polygon_financials"]).

    Returns:
        {
            "ticker":          str,
            "m_score":         float | None,
            "gate_result":     "EXCLUDED" | "FLAGGED" | "CLEAN" | "INSUFFICIENT_DATA",
            "missing_fields":  list[str],   # populated when key inputs are None
        }
    """
    result = {
        "ticker": ticker.upper(),
        "m_score": None,
        "gate_result": "INSUFFICIENT_DATA",
        "missing_fields": [],
    }

    fy_rows = _extract_fy_periods(polygon_financials)
    if len(fy_rows) < 2:
        logger.debug("%s: fewer than 2 FY periods available — skipping Beneish", ticker)
        return result

    t  = _extract_fields(fy_rows[0])  # current year
    t1 = _extract_fields(fy_rows[1])  # prior year

    missing: list[str] = []

    # ── DSRI: Days Sales Receivables Index ───────────────────────────────────
    # (Receivables[t] / Revenue[t]) / (Receivables[t-1] / Revenue[t-1])
    dsri_t  = _safe_div(t["receivables"],  t["revenue"])
    dsri_t1 = _safe_div(t1["receivables"], t1["revenue"])
    DSRI = _safe_div(dsri_t, dsri_t1)
    if DSRI is None:
        missing.append("DSRI")

    # ── GMI: Gross Margin Index ───────────────────────────────────────────────
    # ((Rev[t-1] - COGS[t-1]) / Rev[t-1]) / ((Rev[t] - COGS[t]) / Rev[t])
    gm_t: Optional[float] = None
    if t["revenue"] and t["revenue"] != 0:
        if t.get("gross_profit") is not None:
            gm_t = t["gross_profit"] / t["revenue"]
        elif t.get("cogs") is not None:
            gm_t = (t["revenue"] - t["cogs"]) / t["revenue"]

    gm_t1: Optional[float] = None
    if t1["revenue"] and t1["revenue"] != 0:
        if t1.get("gross_profit") is not None:
            gm_t1 = t1["gross_profit"] / t1["revenue"]
        elif t1.get("cogs") is not None:
            gm_t1 = (t1["revenue"] - t1["cogs"]) / t1["revenue"]

    GMI = _safe_div(gm_t1, gm_t)
    if GMI is None:
        missing.append("GMI")

    # ── AQI: Asset Quality Index ──────────────────────────────────────────────
    # (1 − (PPE[t] + CA[t]) / TA[t]) / (1 − (PPE[t-1] + CA[t-1]) / TA[t-1])
    def _aqi_ratio(f: dict) -> Optional[float]:
        ta = f["total_assets"]
        ppe = f["ppe_net"]
        ca = f["current_assets"]
        if ta is None or ta == 0:
            return None
        # PPE and Current Assets can be 0, but if both are None, ratio is None
        if ppe is None and ca is None:
            return None
        sum_assets = (ppe or 0) + (ca or 0)
        return 1 - (sum_assets / ta)

    aqi_t  = _aqi_ratio(t)
    aqi_t1 = _aqi_ratio(t1)
    AQI = _safe_div(aqi_t, aqi_t1)
    if AQI is None:
        missing.append("AQI")

    # ── SGI: Sales Growth Index ───────────────────────────────────────────────
    SGI = _safe_div(t["revenue"], t1["revenue"])
    if SGI is None:
        missing.append("SGI")

    # ── DEPI: Depreciation Index ──────────────────────────────────────────────
    # (D&A[t-1] / (D&A[t-1] + PPE[t-1])) / (D&A[t] / (D&A[t] + PPE[t]))
    def _depi_ratio(f: dict) -> Optional[float]:
        dep = f["depreciation"]
        ppe = f["ppe_net"]
        if dep is None or (dep == 0 and (ppe is None or ppe == 0)):
            return None
        denom = (dep or 0) + (ppe or 0)
        if denom == 0:
            return None
        return dep / denom

    depi_t  = _depi_ratio(t)
    depi_t1 = _depi_ratio(t1)
    DEPI = _safe_div(depi_t1, depi_t)
    if DEPI is None:
        missing.append("DEPI")
        DEPI = 1.0  # neutral default — D&A fields often missing; don't penalise

    # ── SGAI: SGA Index ───────────────────────────────────────────────────────
    sgai_t  = _safe_div(t["sga"],  t["revenue"])
    sgai_t1 = _safe_div(t1["sga"], t1["revenue"])
    SGAI = _safe_div(sgai_t, sgai_t1)
    if SGAI is None:
        missing.append("SGAI")
        SGAI = 1.0  # neutral default

    # ── TATA: Total Accruals to Total Assets ─────────────────────────────────
    # (Net Income[t] - CFO[t]) / Total Assets[t]
    if t["net_income"] is not None and t["cfo"] is not None and t["total_assets"]:
        TATA = (t["net_income"] - t["cfo"]) / t["total_assets"]
    else:
        TATA = None
        missing.append("TATA")

    # ── LVGI: Leverage Index ──────────────────────────────────────────────────
    # (LTD[t] + CL[t]) / TA[t]) / ((LTD[t-1] + CL[t-1]) / TA[t-1])
    def _lvgi_ratio(f: dict) -> Optional[float]:
        ta = f["total_assets"]
        ltd = f["ltd"]
        cl = f["current_liabilities"]
        if ta is None or ta == 0:
            return None
        if ltd is None and cl is None:
            return None
        return ((ltd or 0) + (cl or 0)) / ta

    lvgi_t  = _lvgi_ratio(t)
    lvgi_t1 = _lvgi_ratio(t1)
    LVGI = _safe_div(lvgi_t, lvgi_t1)
    if LVGI is None:
        missing.append("LVGI")

    result["missing_fields"] = missing

    # ── Require core inputs; fail gracefully if too many are missing ──────────
    core_inputs = [DSRI, GMI, AQI, SGI, TATA, LVGI]
    if sum(x is None for x in core_inputs) > 3:
        logger.debug("%s: too many missing Beneish inputs (%s) — INSUFFICIENT_DATA", ticker, missing)
        return result

    # Substitute neutral values (1.0 for ratios, 0.0 for TATA) for remaining None
    DSRI  = DSRI  if DSRI  is not None else 1.0
    GMI   = GMI   if GMI   is not None else 1.0
    AQI   = AQI   if AQI   is not None else 1.0
    SGI   = SGI   if SGI   is not None else 1.0
    TATA  = TATA  if TATA  is not None else 0.0
    LVGI  = LVGI  if LVGI  is not None else 1.0

    m = (
        _INTERCEPT
        + _COEFF["DSRI"]  * DSRI
        + _COEFF["GMI"]   * GMI
        + _COEFF["AQI"]   * AQI
        + _COEFF["SGI"]   * SGI
        + _COEFF["DEPI"]  * DEPI
        + _COEFF["SGAI"]  * SGAI
        + _COEFF["TATA"]  * TATA
        + _COEFF["LVGI"]  * LVGI
    )
    result["m_score"] = round(m, 4)

    if m > -1.78:
        result["gate_result"] = "EXCLUDED"
    elif m > -2.22:
        result["gate_result"] = "FLAGGED"
    else:
        result["gate_result"] = "CLEAN"

    logger.debug("%s: Beneish M=%.4f → %s", ticker, m, result["gate_result"])
    return result
