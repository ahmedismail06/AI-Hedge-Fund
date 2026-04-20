"""
Earnings quality analysis: Beneish M-score (reuse from screener) + accruals ratio
+ revenue quality flags.

Delegates Beneish computation to backend.screener.factors.earnings_quality.compute_beneish.
Adds accruals ratio and revenue quality signal on top.
"""

import logging
from typing import Optional, Literal

from dotenv import load_dotenv

from backend.screener.factors.earnings_quality import compute_beneish
from backend.financial_modeling.schemas import EarningsQualityResult

load_dotenv()

logger = logging.getLogger(__name__)


def compute_accruals_ratio(
    net_income: Optional[float],
    cfo: Optional[float],
    total_assets: Optional[float],
) -> Optional[float]:
    """
    Compute the accruals ratio: (net_income - cfo) / total_assets.

    A higher positive ratio signals more accrual-based (lower-quality) earnings.

    Args:
        net_income: Net income for the period.
        cfo: Cash flow from operations for the period.
        total_assets: Total assets (balance sheet).

    Returns:
        Accruals ratio as a decimal, or None if any argument is None or total_assets == 0.
    """
    if net_income is None or cfo is None or total_assets is None or total_assets == 0:
        return None
    return (net_income - cfo) / total_assets


def check_revenue_quality(
    revenues_current: Optional[float],
    revenues_prior: Optional[float],
    ebitda_margin_current: Optional[float],
    ebitda_margin_prior: Optional[float],
    sector: Optional[str] = None,
    deferred_revenue_current: Optional[float] = None,
    deferred_revenue_prior: Optional[float] = None,
) -> Optional[str]:
    """
    Detect revenue quality issues or positive signals.

    Checks:
      1. High revenue growth with margin contraction → "HIGH_GROWTH_MARGIN_CONTRACTION"
      2. For SaaS/tech sectors: deferred revenue growing faster than revenue →
         "DEFERRED_GROWTH_HEALTHY" (indicates strong future revenue recognition)

    Args:
        revenues_current: Current period revenue.
        revenues_prior: Prior period revenue.
        ebitda_margin_current: Current period EBITDA margin (as decimal).
        ebitda_margin_prior: Prior period EBITDA margin (as decimal).
        sector: Company sector string (used to detect SaaS/tech).
        deferred_revenue_current: Current period deferred revenue balance.
        deferred_revenue_prior: Prior period deferred revenue balance.

    Returns:
        A flag string or None if no signals detected.
    """
    rev_growth: Optional[float] = None

    if (
        revenues_current is not None
        and revenues_prior is not None
        and revenues_prior > 0
    ):
        rev_growth = (revenues_current - revenues_prior) / revenues_prior

        if (
            rev_growth > 0.30
            and ebitda_margin_current is not None
            and ebitda_margin_prior is not None
            and ebitda_margin_current < ebitda_margin_prior
        ):
            return "HIGH_GROWTH_MARGIN_CONTRACTION"

    saas_sectors = {"Technology", "SaaS", "Healthcare Technology"}
    if (
        sector in saas_sectors
        and deferred_revenue_current is not None
        and deferred_revenue_prior is not None
        and deferred_revenue_prior > 0
        and rev_growth is not None
    ):
        deferred_growth = (
            deferred_revenue_current - deferred_revenue_prior
        ) / deferred_revenue_prior
        if deferred_growth > rev_growth:
            return "DEFERRED_GROWTH_HEALTHY"

    return None


def _derive_quality_grade(
    beneish_gate: str,
    accruals_ratio: Optional[float],
    revenue_quality_flag: Optional[str],
) -> Literal["A", "B", "C", "D", "N/A"]:
    """
    Map Beneish gate, accruals ratio, and revenue quality flag to a single letter grade.

    Grade hierarchy (evaluated in order):
      D   — EXCLUDED
      N/A — INSUFFICIENT_DATA with no accruals data
      C   — FLAGGED OR high accruals ratio (> 0.07)
      B   — CLEAN + mid-range accruals (0.03–0.07)
      B   — CLEAN + HIGH_GROWTH_MARGIN_CONTRACTION flag
      A   — CLEAN + low accruals (< 0.03 or None) + no margin contraction flag
      N/A — fallback

    Args:
        beneish_gate: One of EXCLUDED | FLAGGED | CLEAN | INSUFFICIENT_DATA.
        accruals_ratio: Accruals ratio decimal or None.
        revenue_quality_flag: Revenue quality flag string or None.

    Returns:
        Single letter grade literal.
    """
    if beneish_gate == "EXCLUDED":
        return "D"

    if beneish_gate == "INSUFFICIENT_DATA" and accruals_ratio is None:
        return "N/A"

    if beneish_gate == "FLAGGED" or (
        accruals_ratio is not None and accruals_ratio > 0.07
    ):
        return "C"

    if beneish_gate == "CLEAN" and accruals_ratio is not None and 0.03 <= accruals_ratio <= 0.07:
        return "B"

    if beneish_gate == "CLEAN" and revenue_quality_flag == "HIGH_GROWTH_MARGIN_CONTRACTION":
        return "B"

    if beneish_gate == "CLEAN" and (
        accruals_ratio is None or accruals_ratio < 0.03
    ) and revenue_quality_flag != "HIGH_GROWTH_MARGIN_CONTRACTION":
        return "A"

    return "N/A"


def run_earnings_quality(
    ticker: str,
    polygon_financials: dict,
    fmp_data: dict,
    sector: Optional[str] = None,
) -> EarningsQualityResult:
    """
    Run full earnings quality analysis for a ticker.

    Combines Beneish M-score (fraud detection), accruals ratio (earnings quality),
    and revenue quality checks into a single structured result.

    Args:
        ticker: Stock ticker symbol.
        polygon_financials: Raw Polygon /vX/reference/financials response dict.
        fmp_data: Dict from fetch_fmp() (used for net_income, ttm_operating_cash_flow).
        sector: Optional sector string for SaaS deferred-revenue check.

    Returns:
        EarningsQualityResult with all quality signals populated.
    """
    try:
        # Step 1–2: Beneish M-score
        beneish_result = compute_beneish(ticker, polygon_financials)
        beneish_gate: str = beneish_result.get("gate_result", "INSUFFICIENT_DATA")
        m_score: Optional[float] = beneish_result.get("m_score")

        # Step 3–4: Accruals ratio
        net_income: Optional[float] = fmp_data.get("net_income")
        cfo: Optional[float] = fmp_data.get("ttm_operating_cash_flow")

        total_assets: Optional[float] = None
        try:
            fy_results = [
                r for r in polygon_financials.get("results", [])
                if r.get("fiscal_period") == "FY"
            ]
            if fy_results:
                fy_results.sort(key=lambda r: r.get("filing_date", ""), reverse=True)
                total_assets = (
                    fy_results[0]
                    .get("financials", {})
                    .get("balance_sheet", {})
                    .get("assets", {})
                    .get("value")
                )
        except Exception as exc:
            logger.warning("run_earnings_quality(%s): total_assets extraction failed — %s", ticker, exc)

        accruals_ratio = compute_accruals_ratio(net_income, cfo, total_assets)

        # Step 5–6: Revenue quality
        revenues_current: Optional[float] = None
        revenues_prior: Optional[float] = None
        ebitda_margin_current: Optional[float] = None
        ebitda_margin_prior: Optional[float] = None

        try:
            fy_results_rev = [
                r for r in polygon_financials.get("results", [])
                if r.get("fiscal_period") == "FY"
            ]
            fy_results_rev.sort(key=lambda r: r.get("filing_date", ""), reverse=True)

            if len(fy_results_rev) >= 1:
                inc0 = fy_results_rev[0].get("financials", {}).get("income_statement", {})
                revenues_current = inc0.get("revenues", {}).get("value")
                ebitda_raw_0 = (
                    inc0.get(
                        "earnings_before_interest_taxes_depreciation_and_amortization", {}
                    ).get("value")
                    or inc0.get("operating_income", {}).get("value")
                )
                if ebitda_raw_0 is not None and revenues_current and revenues_current > 0:
                    ebitda_margin_current = ebitda_raw_0 / revenues_current

            if len(fy_results_rev) >= 2:
                inc1 = fy_results_rev[1].get("financials", {}).get("income_statement", {})
                revenues_prior = inc1.get("revenues", {}).get("value")
                ebitda_raw_1 = (
                    inc1.get(
                        "earnings_before_interest_taxes_depreciation_and_amortization", {}
                    ).get("value")
                    or inc1.get("operating_income", {}).get("value")
                )
                if ebitda_raw_1 is not None and revenues_prior and revenues_prior > 0:
                    ebitda_margin_prior = ebitda_raw_1 / revenues_prior

        except Exception as exc:
            logger.warning(
                "run_earnings_quality(%s): revenue extraction for quality check failed — %s",
                ticker,
                exc,
            )

        revenue_quality_flag = check_revenue_quality(
            revenues_current=revenues_current,
            revenues_prior=revenues_prior,
            ebitda_margin_current=ebitda_margin_current,
            ebitda_margin_prior=ebitda_margin_prior,
            sector=sector or fmp_data.get("sector"),
        )

        # Step 7: Quality grade
        quality_grade = _derive_quality_grade(beneish_gate, accruals_ratio, revenue_quality_flag)

        # Step 8: Return result
        return EarningsQualityResult(
            beneish_gate=beneish_gate,
            m_score=m_score,
            accruals_ratio=accruals_ratio,
            revenue_quality_flag=revenue_quality_flag,
            quality_grade=quality_grade,
            unavailable=False,
        )

    except Exception as exc:
        logger.error("run_earnings_quality(%s): unexpected failure — %s", ticker, exc)
        return EarningsQualityResult(
            beneish_gate="INSUFFICIENT_DATA",
            unavailable=True,
        )
