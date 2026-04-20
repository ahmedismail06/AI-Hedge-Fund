"""
DCF model — 3-scenario (bull/base/bear) intrinsic value calculator.
Inputs come entirely from fetch_fmp() extended output. No LLM calls.
"""

import logging
from typing import Optional

import yfinance as yf
from dotenv import load_dotenv

from backend.financial_modeling.schemas import DCFResult, DCFScenario

load_dotenv()

logger = logging.getLogger(__name__)

MARKET_RISK_PREMIUM = 0.055
DEFAULT_COST_OF_DEBT = 0.060
CORPORATE_TAX_RATE = 0.21
DEFAULT_BETA = 1.2
DEFAULT_RISK_FREE_RATE = 0.045
TERMINAL_GROWTH_BY_REGIME = {
    "Risk-On": 0.030,
    "Transitional": 0.025,
    "Risk-Off": 0.020,
    "Stagflation": 0.015,
}

_UNAVAILABLE_SCENARIO = DCFScenario(
    revenue_growth_rate=0.0,
    ebitda_margin=0.0,
    price_target=0.0,
)


def _unavailable_result(reason: str) -> DCFResult:
    """Return a DCFResult marked as unavailable with a human-readable reason."""
    return DCFResult(
        bull=_UNAVAILABLE_SCENARIO,
        base=_UNAVAILABLE_SCENARIO,
        bear=_UNAVAILABLE_SCENARIO,
        wacc=0.0,
        terminal_growth=0.0,
        key_drivers=[],
        unavailable=True,
        unavailable_reason=reason,
    )


def _extract_fy_rows(polygon_financials_raw: dict) -> list[dict]:
    """
    Extract results filtered to fiscal_period == 'FY', sorted newest first.

    Args:
        polygon_financials_raw: Raw JSON response from Polygon /vX/reference/financials.

    Returns:
        List of FY period dicts sorted descending by filing_date. Empty on any error.
    """
    try:
        results = polygon_financials_raw.get("results", [])
        fy_rows = [r for r in results if r.get("fiscal_period") == "FY"]
        fy_rows.sort(key=lambda r: r.get("filing_date", ""), reverse=True)
        return fy_rows
    except Exception as exc:
        logger.warning("_extract_fy_rows: failed — %s", exc)
        return []


def _compute_revenue_cagr(fy_rows: list[dict]) -> Optional[float]:
    """
    Compute 2-3 year revenue CAGR from FY rows.

    Uses the newest and oldest available rows. Returns None if <2 rows or
    revenue extraction fails.

    Args:
        fy_rows: FY period rows sorted newest first.

    Returns:
        CAGR as decimal (e.g. 0.12 for 12%), or None.
    """
    if len(fy_rows) < 2:
        return None
    try:
        newest = fy_rows[0]
        oldest = fy_rows[-1]

        def _get_revenue(row: dict) -> Optional[float]:
            try:
                return row["financials"]["income_statement"]["revenues"]["value"]
            except (KeyError, TypeError):
                return None

        rev_new = _get_revenue(newest)
        rev_old = _get_revenue(oldest)

        if rev_new is None or rev_old is None or rev_old <= 0 or rev_new <= 0:
            return None

        # Number of years between the two periods
        n_years = len(fy_rows) - 1
        if n_years <= 0:
            return None

        cagr = (rev_new / rev_old) ** (1.0 / n_years) - 1.0
        return cagr
    except Exception as exc:
        logger.warning("_compute_revenue_cagr: failed — %s", exc)
        return None


def _blend_growth_rate(
    historical_cagr: Optional[float],
    consensus_rev_current: Optional[float],
    consensus_rev_next: Optional[float],
    latest_revenue: Optional[float],
) -> Optional[float]:
    """
    Blend historical CAGR with consensus-implied next-year growth.

    consensus_rev_current and consensus_rev_next are in millions from yfinance.
    latest_revenue is raw dollars from Polygon.

    Returns blended growth rate clamped to [-0.30, 0.60], or None if no inputs.
    """
    consensus_implied: Optional[float] = None

    if (
        consensus_rev_next is not None
        and latest_revenue is not None
        and latest_revenue > 0
    ):
        consensus_rev_next_raw = consensus_rev_next * 1_000_000
        consensus_implied = (consensus_rev_next_raw - latest_revenue) / latest_revenue

    blended: Optional[float] = None
    if consensus_implied is not None and historical_cagr is not None:
        blended = 0.50 * consensus_implied + 0.50 * historical_cagr
    elif historical_cagr is not None:
        blended = historical_cagr
    elif consensus_implied is not None:
        blended = consensus_implied

    if blended is None:
        return None

    return max(-0.30, min(0.60, blended))


def _compute_wacc(
    beta: Optional[float],
    risk_free_rate: float,
    market_cap: Optional[float],
    total_debt: Optional[float],
    interest_expense: Optional[float],
) -> float:
    """
    Compute Weighted Average Cost of Capital (WACC).

    Uses CAPM for cost of equity. Cost of debt inferred from interest expense / total debt
    when available, otherwise falls back to DEFAULT_COST_OF_DEBT.

    Returns WACC clamped to [0.06, 0.25].
    """
    b = beta if beta is not None else DEFAULT_BETA
    cost_of_equity = risk_free_rate + b * MARKET_RISK_PREMIUM

    if interest_expense and total_debt and total_debt > 0:
        cost_of_debt_pretax = interest_expense / total_debt
    else:
        cost_of_debt_pretax = DEFAULT_COST_OF_DEBT

    cost_of_debt_aftertax = cost_of_debt_pretax * (1.0 - CORPORATE_TAX_RATE)

    e = market_cap if market_cap else 1.0
    d = total_debt if total_debt else 0.0
    v = e + d

    wacc = (e / v) * cost_of_equity + (d / v) * cost_of_debt_aftertax
    return max(0.06, min(0.25, wacc))


def _project_fcff(
    base_revenue: float,
    revenue_growth_rate: float,
    ebitda_margin: float,
    capex_pct_revenue: float,
    years: int = 5,
) -> list[float]:
    """
    Project Free Cash Flow to Firm (FCFF) for each year.

    Each year t (1-indexed):
      revenue_t = base_revenue * (1 + revenue_growth_rate) ** t
      ebitda_t  = revenue_t * ebitda_margin
      fcff_t    = ebitda_t * (1 - CORPORATE_TAX_RATE) - revenue_t * capex_pct_revenue

    FCFF is floored at 0 (going-concern assumption).

    Returns list of `years` floats.
    """
    projected = []
    for t in range(1, years + 1):
        rev_t = base_revenue * (1.0 + revenue_growth_rate) ** t
        ebitda_t = rev_t * ebitda_margin
        fcff_t = ebitda_t * (1.0 - CORPORATE_TAX_RATE) - rev_t * capex_pct_revenue
        projected.append(max(0.0, fcff_t))
    return projected


def _terminal_value(
    final_year_fcff: float,
    wacc: float,
    terminal_growth: float,
) -> float:
    """
    Compute Gordon Growth Model terminal value.

    Returns 0.0 if wacc <= terminal_growth (perpetuity formula undefined).
    """
    if wacc <= terminal_growth:
        return 0.0
    return final_year_fcff * (1.0 + terminal_growth) / (wacc - terminal_growth)


def _dcf_price_target(
    projected_fcff: list[float],
    terminal_value: float,
    wacc: float,
    net_debt: float,
    shares_outstanding: float,
) -> float:
    """
    Compute intrinsic price per share.

    Discounts each FCFF and the terminal value back to present, sums to get
    enterprise value, subtracts net debt for equity value, divides by shares.

    Returns price per share, floored at 0.01.
    """
    n = len(projected_fcff)

    pv_fcff = sum(
        fcff_t / (1.0 + wacc) ** t
        for t, fcff_t in enumerate(projected_fcff, start=1)
    )
    pv_tv = terminal_value / (1.0 + wacc) ** n

    enterprise_value = pv_fcff + pv_tv
    equity_value = max(0.0, enterprise_value - net_debt)

    if shares_outstanding <= 0:
        return 0.01

    price = equity_value / shares_outstanding
    return max(0.01, price)


def run_dcf(
    ticker: str,
    fmp_data: dict,
    macro_regime: str = "Transitional",
) -> DCFResult:
    """
    Run a 3-scenario DCF (bull/base/bear) for the given ticker.

    All inputs come from fmp_data (output of fetch_fmp() extended with _risk_free_rate).
    Never raises — returns DCFResult with unavailable=True on any critical failure.

    Args:
        ticker: Stock ticker symbol.
        fmp_data: Dict from fetch_fmp() with optional '_risk_free_rate' injected.
        macro_regime: Current macro regime string for terminal growth selection.

    Returns:
        DCFResult with three scenario price targets and key metadata.
    """
    try:
        # Step 1: Polygon financials
        polygon_financials_raw = fmp_data.get("polygon_financials_raw")
        if polygon_financials_raw is None:
            return _unavailable_result("no_polygon_data")

        # Step 2: FY rows
        fy_rows = _extract_fy_rows(polygon_financials_raw)
        if not fy_rows:
            return _unavailable_result("no_fy_rows")

        # Step 3: Base revenue
        try:
            base_revenue = fy_rows[0]["financials"]["income_statement"]["revenues"]["value"]
        except (KeyError, TypeError):
            base_revenue = None
        if base_revenue is None:
            return _unavailable_result("no_revenue_data")

        # Step 4: EBITDA
        try:
            ebitda = fy_rows[0]["financials"]["income_statement"][
                "earnings_before_interest_taxes_depreciation_and_amortization"
            ]["value"]
        except (KeyError, TypeError):
            ebitda = None

        if ebitda is None:
            try:
                ebitda = fy_rows[0]["financials"]["income_statement"][
                    "operating_income"
                ]["value"]
            except (KeyError, TypeError):
                ebitda = None

        if ebitda is None:
            ebitda = base_revenue * 0.15

        # Step 5: CapEx
        try:
            capex = fy_rows[0]["financials"]["cash_flow_statement"][
                "capital_expenditure"
            ]["value"]
        except (KeyError, TypeError):
            capex = None

        if capex is not None and base_revenue > 0:
            capex_pct_revenue = abs(capex) / base_revenue
        else:
            capex_pct_revenue = 0.05

        # Step 6: Shares outstanding
        shares_outstanding: Optional[float] = None
        try:
            shares_outstanding = fy_rows[0]["financials"]["balance_sheet"][
                "common_shares_outstanding"
            ]["value"]
        except (KeyError, TypeError):
            shares_outstanding = None

        if shares_outstanding is None:
            try:
                yf_info = yf.Ticker(ticker).info or {}
                shares_outstanding = yf_info.get("sharesOutstanding")
                if shares_outstanding is not None:
                    shares_outstanding = float(shares_outstanding)
            except Exception as exc:
                logger.warning("run_dcf(%s): yfinance shares fallback failed — %s", ticker, exc)
                shares_outstanding = None

        if shares_outstanding is None:
            return _unavailable_result("no_shares_data")

        # Steps 7–13: Capital structure and rates
        market_cap: Optional[float] = fmp_data.get("market_cap")
        total_debt: Optional[float] = fmp_data.get("long_term_debt")
        cash: float = fmp_data.get("cash") or 0.0
        net_debt: float = (total_debt or 0.0) - cash
        beta: Optional[float] = fmp_data.get("beta")
        interest_expense: Optional[float] = fmp_data.get("interest_expense")
        risk_free_rate: float = fmp_data.get("_risk_free_rate", DEFAULT_RISK_FREE_RATE)

        # Step 14: EBITDA margin
        raw_margin = ebitda / base_revenue if base_revenue > 0 else 0.0
        ebitda_margin = max(0.0, min(0.80, raw_margin))

        # Step 15: WACC
        wacc = _compute_wacc(beta, risk_free_rate, market_cap, total_debt, interest_expense)

        # Step 16: Terminal growth
        terminal_growth = TERMINAL_GROWTH_BY_REGIME.get(macro_regime, 0.025)

        # Step 17: Historical CAGR
        historical_cagr = _compute_revenue_cagr(fy_rows)

        # Step 18: Blended base growth
        base_growth = _blend_growth_rate(
            historical_cagr,
            fmp_data.get("consensus_revenue_current_year"),
            fmp_data.get("consensus_revenue_next_year"),
            base_revenue,
        )
        if base_growth is None:
            base_growth = 0.07

        # Step 19: Scenarios
        bull_growth = min(base_growth + 0.02, 0.60)
        bull_margin = min(ebitda_margin + 0.02, 0.80)
        bear_growth = max(base_growth - 0.03, -0.20)
        bear_margin = max(ebitda_margin - 0.015, 0.0)

        def _run_scenario(growth: float, margin: float) -> DCFScenario:
            projected = _project_fcff(base_revenue, growth, margin, capex_pct_revenue)
            tv = _terminal_value(projected[-1], wacc, terminal_growth)
            price = _dcf_price_target(projected, tv, wacc, net_debt, shares_outstanding)
            return DCFScenario(
                revenue_growth_rate=round(growth, 4),
                ebitda_margin=round(margin, 4),
                price_target=round(price, 2),
            )

        # Step 20: Compute all scenarios
        base_scenario = _run_scenario(base_growth, ebitda_margin)
        bull_scenario = _run_scenario(bull_growth, bull_margin)
        bear_scenario = _run_scenario(bear_growth, bear_margin)

        # Step 21: Key drivers
        key_drivers = [
            f"Revenue CAGR: {base_growth * 100:.1f}%",
            f"EBITDA margin: {ebitda_margin * 100:.1f}%",
            f"WACC: {wacc * 100:.1f}%",
        ]

        # Step 22: Return result
        return DCFResult(
            bull=bull_scenario,
            base=base_scenario,
            bear=bear_scenario,
            wacc=round(wacc, 4),
            terminal_growth=round(terminal_growth, 4),
            shares_outstanding=shares_outstanding,
            key_drivers=key_drivers,
            unavailable=False,
        )

    except Exception as exc:
        logger.error("run_dcf(%s): unexpected failure — %s", ticker, exc)
        return _unavailable_result(f"unexpected_error: {exc}")
