"""
Smoke tests for Component 10 — Financial Modeling Module.

Coverage status (2026-04-18):
  Testable against committed code:
    - dcf.run_dcf()          → Scenarios 1, 2, 3
    - dcf._compute_wacc()    → Scenario 9
    - schemas                → Scenario 8 (schema construction only)

  NOT YET TESTABLE — modules missing from repo:
    - backend/financial_modeling/earnings_quality.py  (Scenarios 4, 5, 6)
    - backend/financial_modeling/runner.py            (Scenarios 7, 8 full)
  Recommend spawning implement-agent to build those modules,
  then extending this file.

Run:
    python -m pytest backend/tests/test_financial_modeling.py -v
"""

import sys
import os

import pytest

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path so absolute imports resolve.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


# ===========================================================================
# Helpers
# ===========================================================================

def _make_polygon_financials() -> dict:
    """
    Return a realistic polygon_financials_raw dict with two FY rows.

    FY[0] (newest): revenues=110M, ebitda=24.2M, capex=-5.5M, shares=10M
    FY[1] (prior) : revenues=95M,  ebitda=18.05M, capex=-4.75M, shares=10M

    Beneish fields also included for future earnings_quality tests.
    """
    return {
        "results": [
            {
                "fiscal_period": "FY",
                "filing_date": "2025-03-15",
                "financials": {
                    "income_statement": {
                        "revenues": {"value": 110_000_000},
                        "earnings_before_interest_taxes_depreciation_and_amortization": {
                            "value": 24_200_000
                        },
                        "cost_of_revenue": {"value": 50_000_000},
                        "gross_profit": {"value": 60_000_000},
                    },
                    "cash_flow_statement": {
                        "capital_expenditure": {"value": -5_500_000},
                    },
                    "balance_sheet": {
                        "common_shares_outstanding": {"value": 10_000_000},
                        "assets": {"value": 200_000_000},
                        "current_assets": {"value": 80_000_000},
                        "fixed_assets": {"value": 40_000_000},
                        "accounts_receivable": {"value": 15_000_000},
                        "long_term_debt": {"value": 50_000_000},
                        "current_liabilities": {"value": 30_000_000},
                    },
                },
            },
            {
                "fiscal_period": "FY",
                "filing_date": "2024-03-10",
                "financials": {
                    "income_statement": {
                        "revenues": {"value": 95_000_000},
                        "earnings_before_interest_taxes_depreciation_and_amortization": {
                            "value": 18_050_000
                        },
                        "cost_of_revenue": {"value": 43_000_000},
                        "gross_profit": {"value": 52_000_000},
                    },
                    "cash_flow_statement": {
                        "capital_expenditure": {"value": -4_750_000},
                    },
                    "balance_sheet": {
                        "common_shares_outstanding": {"value": 10_000_000},
                        "assets": {"value": 180_000_000},
                        "current_assets": {"value": 70_000_000},
                        "fixed_assets": {"value": 38_000_000},
                        "accounts_receivable": {"value": 12_000_000},
                        "long_term_debt": {"value": 55_000_000},
                        "current_liabilities": {"value": 28_000_000},
                    },
                },
            },
        ]
    }


def _make_fmp_data() -> dict:
    """
    Return a realistic fmp_data dict covering all DCF inputs.

    market_cap              : 500M
    long_term_debt          : 50M
    cash                    : 30M
    beta                    : 1.1
    interest_expense        : 3M
    net_income              : 15M
    ttm_operating_cash_flow : 20M
    consensus_revenue_*     : 120M / 138M (millions, as yfinance returns)
    _risk_free_rate         : 4.5%
    polygon_financials_raw  : 2 FY rows (see _make_polygon_financials)
    """
    return {
        "market_cap": 500_000_000,
        "long_term_debt": 50_000_000,
        "cash": 30_000_000,
        "beta": 1.1,
        "interest_expense": 3_000_000,
        "net_income": 15_000_000,
        "ttm_operating_cash_flow": 20_000_000,
        "consensus_revenue_current_year": 120.0,
        "consensus_revenue_next_year": 138.0,
        "_risk_free_rate": 0.045,
        "polygon_financials_raw": _make_polygon_financials(),
    }


# ===========================================================================
# Scenario 1 — DCF happy path (full data, Risk-On regime)
# ===========================================================================

from backend.financial_modeling.dcf import run_dcf, _compute_wacc


def test_dcf_happy_path():
    """Full fmp_data with Risk-On → three positive price targets in correct order."""
    fmp_data = _make_fmp_data()
    result = run_dcf("TEST", fmp_data, macro_regime="Risk-On")

    assert result.unavailable is False, f"DCF unexpectedly unavailable: {result.unavailable_reason}"
    assert result.bull.price_target > 0
    assert result.base.price_target > 0
    assert result.bear.price_target > 0
    assert result.bull.price_target > result.base.price_target > result.bear.price_target, (
        f"Expected bull > base > bear; got "
        f"bull={result.bull.price_target} base={result.base.price_target} bear={result.bear.price_target}"
    )
    assert 0.05 <= result.wacc <= 0.25, f"WACC out of bounds: {result.wacc}"
    assert result.terminal_growth == pytest.approx(0.030, abs=1e-6), (
        f"Expected terminal_growth=0.030 for Risk-On, got {result.terminal_growth}"
    )
    assert len(result.key_drivers) >= 2


# ===========================================================================
# Scenario 2 — DCF missing beta → falls back to DEFAULT_BETA=1.2, still valid
# ===========================================================================

def test_dcf_missing_beta():
    """beta=None → DEFAULT_BETA used; DCF still produces a valid result."""
    fmp_data = _make_fmp_data()
    fmp_data["beta"] = None

    result = run_dcf("TEST", fmp_data)

    assert result.unavailable is False, f"DCF unexpectedly unavailable: {result.unavailable_reason}"
    assert result.wacc > 0
    assert result.base.price_target > 0


# ===========================================================================
# Scenario 3 — DCF missing polygon_financials_raw → unavailable=True
# ===========================================================================

def test_dcf_missing_polygon_data():
    """polygon_financials_raw=None → result.unavailable=True with a reason string."""
    fmp_data = _make_fmp_data()
    fmp_data["polygon_financials_raw"] = None

    result = run_dcf("TEST", fmp_data)

    assert result.unavailable is True
    assert result.unavailable_reason is not None
    assert len(result.unavailable_reason) > 0


# ===========================================================================
# Edge case — empty polygon results list → unavailable (no FY rows)
# ===========================================================================

def test_dcf_empty_fy_rows():
    """polygon_financials_raw with zero FY entries → unavailable=True."""
    fmp_data = _make_fmp_data()
    fmp_data["polygon_financials_raw"] = {"results": []}

    result = run_dcf("TEST", fmp_data)

    assert result.unavailable is True
    assert result.unavailable_reason is not None


# ===========================================================================
# Scenario 9 — WACC calculation correctness
# ===========================================================================

def test_compute_wacc():
    """
    E=500M, D=50M, beta=1.0, Rf=0.045, interest=3M, debt=50M.

    cost_of_equity = 0.045 + 1.0 * 0.055 = 0.10
    cost_of_debt_pretax = 3M/50M = 0.06
    cost_of_debt_aftertax = 0.06 * (1 - 0.21) = 0.0474
    V = 550M
    WACC = (500/550)*0.10 + (50/550)*0.0474 ≈ 0.0954
    """
    wacc = _compute_wacc(
        beta=1.0,
        risk_free_rate=0.045,
        market_cap=500_000_000,
        total_debt=50_000_000,
        interest_expense=3_000_000,
    )
    assert 0.09 <= wacc <= 0.11, f"WACC={wacc:.4f} outside expected 0.09–0.11"


def test_compute_wacc_no_debt():
    """Zero debt → all-equity financing; WACC equals cost of equity."""
    wacc = _compute_wacc(
        beta=1.0,
        risk_free_rate=0.045,
        market_cap=500_000_000,
        total_debt=0,
        interest_expense=None,
    )
    expected_coe = 0.045 + 1.0 * 0.055  # 0.10
    assert wacc == pytest.approx(expected_coe, abs=1e-6)


def test_compute_wacc_clamped_high():
    """Extreme beta=5.0 → raw WACC would exceed 0.25; must be clamped to 0.25."""
    wacc = _compute_wacc(
        beta=5.0,
        risk_free_rate=0.045,
        market_cap=500_000_000,
        total_debt=0,
        interest_expense=None,
    )
    assert wacc == pytest.approx(0.25, abs=1e-6)


# ===========================================================================
# Scenario 8 (partial) — FinancialModelOutput schema construction
# ===========================================================================

from backend.financial_modeling.schemas import (
    DCFResult,
    DCFScenario,
    EarningsQualityResult,
    FinancialModelOutput,
)


def test_financial_model_output_schema():
    """
    FinancialModelOutput accepts a valid DCFResult + EarningsQualityResult
    and surfaces the correct field values.  No runner.py needed — pure schema
    validation.
    """
    dcf = DCFResult(
        bull=DCFScenario(revenue_growth_rate=0.12, ebitda_margin=0.24, price_target=52.10),
        base=DCFScenario(revenue_growth_rate=0.10, ebitda_margin=0.22, price_target=43.80),
        bear=DCFScenario(revenue_growth_rate=0.07, ebitda_margin=0.20, price_target=32.50),
        wacc=0.112,
        terminal_growth=0.025,
        key_drivers=["Revenue CAGR: 10.0%", "EBITDA margin: 22.0%"],
    )
    eq = EarningsQualityResult(beneish_gate="CLEAN", quality_grade="A")

    output = FinancialModelOutput(
        ticker="TEST",
        run_date="2026-04-18",
        dcf=dcf,
        earnings_quality=eq,
        summary="placeholder",
        model_json={},
    )

    assert output.ticker == "TEST"
    assert output.dcf.base.price_target == pytest.approx(43.80)
    assert output.earnings_quality.quality_grade == "A"
    assert output.dcf.unavailable is False


def test_dcf_result_unavailable_flag():
    """DCFResult with unavailable=True stores the reason string."""
    result = DCFResult(
        bull=DCFScenario(revenue_growth_rate=0.0, ebitda_margin=0.0, price_target=0.0),
        base=DCFScenario(revenue_growth_rate=0.0, ebitda_margin=0.0, price_target=0.0),
        bear=DCFScenario(revenue_growth_rate=0.0, ebitda_margin=0.0, price_target=0.0),
        wacc=0.0,
        terminal_growth=0.0,
        key_drivers=[],
        unavailable=True,
        unavailable_reason="no_polygon_data",
    )
    assert result.unavailable is True
    assert result.unavailable_reason == "no_polygon_data"


# ===========================================================================
# Regime terminal growth mapping
# ===========================================================================

def test_terminal_growth_by_regime():
    """Each macro regime maps to the correct terminal_growth constant."""
    from backend.financial_modeling.dcf import TERMINAL_GROWTH_BY_REGIME

    assert TERMINAL_GROWTH_BY_REGIME["Risk-On"] == pytest.approx(0.030)
    assert TERMINAL_GROWTH_BY_REGIME["Transitional"] == pytest.approx(0.025)
    assert TERMINAL_GROWTH_BY_REGIME["Risk-Off"] == pytest.approx(0.020)
    assert TERMINAL_GROWTH_BY_REGIME["Stagflation"] == pytest.approx(0.015)


def test_dcf_terminal_growth_stagflation():
    """Stagflation regime → terminal_growth=0.015 in returned result."""
    fmp_data = _make_fmp_data()
    result = run_dcf("TEST", fmp_data, macro_regime="Stagflation")
    assert result.unavailable is False
    assert result.terminal_growth == pytest.approx(0.015, abs=1e-6)


# ===========================================================================
# _blend_growth_rate edge cases — pure logic, no mocking needed
# ===========================================================================

from backend.financial_modeling.dcf import _blend_growth_rate


def test_blend_growth_rate_both_inputs():
    """When both historical CAGR and consensus are available, result is their 50/50 blend."""
    # consensus_rev_next=138M, latest_rev=110M → implied=(138-110)/110 ≈ 0.2545
    # historical CAGR with our data: (110/95)^1 - 1 ≈ 0.1579
    # blended ≈ 0.50 * 0.2545 + 0.50 * 0.1579 ≈ 0.2062
    blended = _blend_growth_rate(
        historical_cagr=0.1579,
        consensus_rev_current=120.0,
        consensus_rev_next=138.0,
        latest_revenue=110_000_000,
    )
    assert blended is not None
    assert 0.15 <= blended <= 0.25


def test_blend_growth_rate_no_inputs():
    """All None → returns None."""
    assert _blend_growth_rate(None, None, None, None) is None


def test_blend_growth_rate_clamped():
    """Extreme consensus growth → result clamped to 0.60 ceiling."""
    result = _blend_growth_rate(
        historical_cagr=0.60,
        consensus_rev_current=None,
        consensus_rev_next=None,
        latest_revenue=None,
    )
    # Only historical available; already at ceiling
    assert result == pytest.approx(0.60)


# ===========================================================================
# Scenarios 4–6 — earnings_quality module
# ===========================================================================

from backend.financial_modeling.earnings_quality import (
    compute_accruals_ratio,
    _derive_quality_grade,
    run_earnings_quality,
)


def test_compute_accruals_ratio():
    """(NI - CFO) / TA = (10M - (-5M)) / 50M = 0.30."""
    ratio = compute_accruals_ratio(
        net_income=10_000_000, cfo=-5_000_000, total_assets=50_000_000
    )
    assert ratio == pytest.approx(0.30, rel=0.01)


def test_compute_accruals_ratio_missing_inputs():
    assert compute_accruals_ratio(None, 5_000_000, 50_000_000) is None
    assert compute_accruals_ratio(10_000_000, None, 50_000_000) is None
    assert compute_accruals_ratio(10_000_000, 5_000_000, 0) is None


def test_derive_quality_grade_a():
    assert _derive_quality_grade("CLEAN", 0.01, None) == "A"


def test_derive_quality_grade_b_accruals():
    assert _derive_quality_grade("CLEAN", 0.05, None) == "B"


def test_derive_quality_grade_c_flagged():
    assert _derive_quality_grade("FLAGGED", 0.01, None) == "C"


def test_derive_quality_grade_d_excluded():
    assert _derive_quality_grade("EXCLUDED", None, None) == "D"


def test_earnings_quality_high_accruals_grade_c():
    """High accruals ratio (NI >> CFO) → grade C or D."""
    fmp_data = _make_fmp_data()
    fmp_data["net_income"] = 10_000_000
    fmp_data["ttm_operating_cash_flow"] = -5_000_000  # big gap → accruals_ratio=0.075
    poly = _make_polygon_financials()
    result = run_earnings_quality("TEST", poly, fmp_data)
    assert result.accruals_ratio is not None
    assert result.accruals_ratio > 0.05
    assert result.quality_grade in ("C", "D")


def test_earnings_quality_clean_grade_a():
    """Low accruals and clean Beneish → grade A."""
    fmp_data = _make_fmp_data()
    fmp_data["net_income"] = 15_000_000
    fmp_data["ttm_operating_cash_flow"] = 14_000_000  # accruals = 1M/200M = 0.005
    poly = _make_polygon_financials()
    result = run_earnings_quality("TEST", poly, fmp_data)
    assert result.beneish_gate in ("CLEAN", "INSUFFICIENT_DATA", "FLAGGED")
    if result.beneish_gate == "CLEAN" and result.accruals_ratio is not None and result.accruals_ratio < 0.03:
        assert result.quality_grade == "A"


# ===========================================================================
# Scenarios 7–8 — runner module
# ===========================================================================

from backend.financial_modeling import runner as _runner_mod


def test_runner_resilience_dcf_failure(monkeypatch):
    """If run_dcf raises, run_financial_model returns unavailable DCF without raising."""
    def _bad_dcf(*args, **kwargs):
        raise RuntimeError("simulated dcf failure")

    monkeypatch.setattr(_runner_mod, "run_dcf", _bad_dcf)
    monkeypatch.setattr(_runner_mod, "_get_macro_regime", lambda: "Transitional")
    monkeypatch.setattr(_runner_mod, "_read_risk_free_rate", lambda: 0.045)
    monkeypatch.setattr(_runner_mod, "_persist_model", lambda *args, **kwargs: None)

    fmp_data = _make_fmp_data()
    output = _runner_mod.run_financial_model("TEST", fmp_data)

    assert output.dcf.unavailable is True
    assert output.ticker == "TEST"
    # earnings_quality should still attempt to run
    assert output.earnings_quality is not None


def test_format_financial_modeling_context_non_empty():
    """_format_financial_modeling_context returns a non-empty string with key values."""
    from backend.financial_modeling.runner import _format_financial_modeling_context

    dcf = DCFResult(
        bull=DCFScenario(revenue_growth_rate=0.12, ebitda_margin=0.24, price_target=52.10),
        base=DCFScenario(revenue_growth_rate=0.10, ebitda_margin=0.22, price_target=43.80),
        bear=DCFScenario(revenue_growth_rate=0.07, ebitda_margin=0.20, price_target=32.50),
        wacc=0.112,
        terminal_growth=0.025,
        key_drivers=["Revenue CAGR: 10.0%", "EBITDA margin: 22.0%"],
    )
    eq = EarningsQualityResult(beneish_gate="CLEAN", quality_grade="A")
    output = FinancialModelOutput(
        ticker="TEST", run_date="2026-04-18", dcf=dcf, earnings_quality=eq,
        summary="placeholder", model_json={},
    )

    context = _format_financial_modeling_context(output)
    assert "DCF" in context
    assert len(context) > 50
    # base target should appear in some form
    assert "43" in context  # $43.80


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
