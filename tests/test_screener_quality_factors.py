import pytest
from typing import Optional
from backend.screener.factors.quality import (
    _fmp_roic,
    _polygon_roic_fallback,
    _polygon_fcf_conversion,
    _get_latest_two_fy
)

# ---------------------------------------------------------------------------
# Tests for _fmp_roic
# ---------------------------------------------------------------------------

def test_fmp_roic_empty_inputs():
    """annual_stmts or balance_sheets is empty list → should return None"""
    assert _fmp_roic([], [{"totalStockholdersEquity": 1000}]) is None
    assert _fmp_roic([{"operatingIncome": 100}], []) is None


def test_fmp_roic_missing_operating_income():
    """operatingIncome is None → should return None"""
    stmt = {"operatingIncome": None}
    bs = {"totalStockholdersEquity": 1000}
    assert _fmp_roic([stmt], [bs]) is None


def test_fmp_roic_tax_rate_fallback():
    """incomeBeforeTax = 0 (division by zero guard) → should fall back to 21%"""
    stmt = {
        "operatingIncome": 100,
        "incomeBeforeTax": 0,
        "incomeTaxExpense": 10
    }
    bs = {"totalStockholdersEquity": 1000}
    # NOPAT = 100 * (1 - 0.21) = 79
    # IC = 1000
    # ROIC = 0.079
    result = _fmp_roic([stmt], [bs])
    assert result == pytest.approx(0.079)


def test_fmp_roic_negative_operating_income():
    """Negative operating income (losses) → should return negative ROIC, not None"""
    stmt = {"operatingIncome": -100, "incomeBeforeTax": -100, "incomeTaxExpense": -20}
    bs = {"totalStockholdersEquity": 1000}
    # tax_rate = -20 / -100 = 0.20 (within [0.1, 0.4])
    # NOPAT = -100 * (1 - 0.20) = -80
    # ROIC = -80 / 1000 = -0.08
    result = _fmp_roic([stmt], [bs])
    assert result == pytest.approx(-0.08)


def test_fmp_roic_missing_debt_or_cash_defaults_to_zero():
    """totalDebt or cashAndCashEquivalents is None → should default to 0"""
    stmt = {"operatingIncome": 100, "incomeBeforeTax": 100, "incomeTaxExpense": 21}
    bs = {"totalStockholdersEquity": 1000, "totalDebt": None, "cashAndCashEquivalents": None}
    # tax_rate = 0.21, NOPAT = 79
    # IC = 1000 + 0 - 0 = 1000
    # ROIC = 0.079
    result = _fmp_roic([stmt], [bs])
    assert result == pytest.approx(0.079)


def test_fmp_roic_tax_rate_clamping():
    """Effective tax rate gets clamped (e.g. raw rate = -0.30 or 0.80) → should clamp to [0.10, 0.40]"""
    bs = {"totalStockholdersEquity": 1000}
    
    # Case 1: Low rate
    stmt_low = {"operatingIncome": 100, "incomeBeforeTax": 100, "incomeTaxExpense": -30}
    # raw_rate = -0.3, clamped to 0.1
    # NOPAT = 100 * 0.9 = 90
    assert _fmp_roic([stmt_low], [bs]) == pytest.approx(0.09)

    # Case 2: High rate
    stmt_high = {"operatingIncome": 100, "incomeBeforeTax": 100, "incomeTaxExpense": 80}
    # raw_rate = 0.8, clamped to 0.4
    # NOPAT = 100 * 0.6 = 60
    assert _fmp_roic([stmt_high], [bs]) == pytest.approx(0.06)


def test_fmp_roic_invested_capital_zero_or_negative():
    """invested_capital <= 0 → should return None"""
    stmt = {"operatingIncome": 100}
    bs = {"totalStockholdersEquity": 0, "totalDebt": 0, "cashAndCashEquivalents": 0}
    assert _fmp_roic([stmt], [bs]) is None
    
    bs_neg = {"totalStockholdersEquity": -100, "totalDebt": 50, "cashAndCashEquivalents": 0}
    assert _fmp_roic([stmt], [bs_neg]) is None


# ---------------------------------------------------------------------------
# Tests for _polygon_roic_fallback
# ---------------------------------------------------------------------------

def test_polygon_roic_fallback_normal():
    """Normal case for Polygon ROIC fallback"""
    cur = {
        "operating_income": 100,
        "equity": 800,
        "total_debt": 150,
        "current_debt": 50
    }
    # NOPAT = 100 * 0.79 = 79
    # IC = 800 + 150 + 50 = 1000
    # ROIC = 0.079
    assert _polygon_roic_fallback(cur) == pytest.approx(0.079)


def test_polygon_roic_fallback_missing_inputs():
    """operating_income or equity is None → should return None"""
    assert _polygon_roic_fallback({"operating_income": None, "equity": 1000}) is None
    assert _polygon_roic_fallback({"operating_income": 100, "equity": None}) is None


def test_polygon_roic_fallback_invested_capital_zero_or_negative():
    """invested_capital <= 0 → should return None"""
    cur = {"operating_income": 100, "equity": -100, "total_debt": 50, "current_debt": 50}
    assert _polygon_roic_fallback(cur) is None


# ---------------------------------------------------------------------------
# Tests for _polygon_fcf_conversion
# ---------------------------------------------------------------------------

def test_polygon_fcf_conversion_cfo_none():
    """cfo is None → should return None"""
    assert _polygon_fcf_conversion({"cfo": None}, 100) is None


def test_polygon_fcf_conversion_capex_none():
    """capex is None → should use cfo only"""
    # FCF = CFO = 80
    # Net Income = 100
    # Ratio = 0.8
    assert _polygon_fcf_conversion({"cfo": 80, "capex": None}, 100) == pytest.approx(0.8)


def test_polygon_fcf_conversion_clamping():
    """FCF ratio clamped at upper bound (> 3.0) and lower bound (< -1.0)"""
    # Upper bound
    assert _polygon_fcf_conversion({"cfo": 400, "capex": 0}, 100) == 3.0
    # Lower bound
    assert _polygon_fcf_conversion({"cfo": -200, "capex": 0}, 100) == -1.0


def test_polygon_fcf_conversion_net_income_none_or_negative():
    """net_income is None or <= 0 → should return None"""
    assert _polygon_fcf_conversion({"cfo": 100}, None) is None
    assert _polygon_fcf_conversion({"cfo": 100}, 0) is None
    assert _polygon_fcf_conversion({"cfo": 100}, -50) is None


# ---------------------------------------------------------------------------
# Tests for _get_latest_two_fy
# ---------------------------------------------------------------------------

def test_get_latest_two_fy_missing_cf_statement():
    """cash_flow_statement key missing entirely → cfo and capex should gracefully be None"""
    polygon_data = {
        "results": [
            {
                "fiscal_period": "FY",
                "filing_date": "2024-01-01",
                "financials": {
                    "income_statement": {"revenues": {"value": 100}},
                    "balance_sheet": {"equity": {"value": 500}}
                }
            },
            {
                "fiscal_period": "FY",
                "filing_date": "2023-01-01",
                "financials": {
                    "income_statement": {"revenues": {"value": 90}},
                    "balance_sheet": {"equity": {"value": 450}}
                }
            }
        ]
    }
    cur, prior = _get_latest_two_fy(polygon_data)
    assert cur["revenue"] == 100
    assert cur["cfo"] is None
    assert cur["capex"] is None
    assert prior["revenue"] == 90
    assert prior["cfo"] is None
    assert prior["capex"] is None
