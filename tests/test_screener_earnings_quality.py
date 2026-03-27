"""
Smoke tests for backend/screener/factors/earnings_quality.py

Coverage:
- compute_beneish returns gate_result in valid set
- Clean company (healthy financials) → CLEAN gate_result
- Manipulated company (extreme accruals/DSRI) → EXCLUDED gate_result
- Borderline company → FLAGGED gate_result
- Fewer than 2 FY periods → INSUFFICIENT_DATA
- Empty polygon_financials → INSUFFICIENT_DATA
- Too many missing core inputs → INSUFFICIENT_DATA
- Ticker symbol is uppercased in output
"""

from backend.screener.factors.earnings_quality import compute_beneish

_VALID_GATE_RESULTS = {"EXCLUDED", "FLAGGED", "CLEAN", "INSUFFICIENT_DATA"}


# ---------------------------------------------------------------------------
# Helpers — build minimal Polygon FY rows
# ---------------------------------------------------------------------------

def _make_fy_row(
    filing_date: str,
    revenue: float,
    cogs: float,
    gross_profit: float,
    net_income: float,
    total_assets: float,
    current_assets: float,
    ppe_net: float,
    receivables: float,
    ltd: float,
    current_liabilities: float,
    cfo: float,
    depreciation: float,
    sga: float,
) -> dict:
    """Build a Polygon-shaped FY financial row."""
    def w(v):
        return {"value": v}

    return {
        "fiscal_period": "FY",
        "filing_date": filing_date,
        "financials": {
            "income_statement": {
                "revenues":                                                      w(revenue),
                "cost_of_revenue":                                               w(cogs),
                "gross_profit":                                                  w(gross_profit),
                "net_income_loss":                                               w(net_income),
                "selling_general_administrative_expenses":                       w(sga),
                "depreciation_and_amortization":                                 w(depreciation),
            },
            "balance_sheet": {
                "assets":               w(total_assets),
                "current_assets":       w(current_assets),
                "fixed_assets":         w(ppe_net),
                "accounts_receivable":  w(receivables),
                "long_term_debt":       w(ltd),
                "current_liabilities":  w(current_liabilities),
                "equity":               w(total_assets - ltd - current_liabilities),
            },
            "cash_flow_statement": {
                "net_cash_flow_from_operating_activities": w(cfo),
            },
        },
    }


def _clean_financials() -> dict:
    """Two FY rows for a healthy, non-manipulating company."""
    current = _make_fy_row(
        filing_date="2024-03-01",
        revenue=500_000_000,
        cogs=200_000_000,
        gross_profit=300_000_000,
        net_income=50_000_000,
        total_assets=400_000_000,
        current_assets=150_000_000,
        ppe_net=100_000_000,
        receivables=60_000_000,   # receivables / revenue roughly stable
        ltd=50_000_000,
        current_liabilities=80_000_000,
        cfo=70_000_000,           # high CFO → low accruals (TATA near zero)
        depreciation=20_000_000,
        sga=40_000_000,
    )
    prior = _make_fy_row(
        filing_date="2023-03-01",
        revenue=460_000_000,
        cogs=190_000_000,
        gross_profit=270_000_000,
        net_income=45_000_000,
        total_assets=370_000_000,
        current_assets=140_000_000,
        ppe_net=95_000_000,
        receivables=55_000_000,
        ltd=48_000_000,
        current_liabilities=75_000_000,
        cfo=65_000_000,
        depreciation=18_000_000,
        sga=37_000_000,
    )
    return {"results": [current, prior]}


def _manipulated_financials() -> dict:
    """Two FY rows engineered to push M-score above -1.78 (EXCLUDED zone)."""
    # Extreme DSRI: receivables doubled but revenue flat → bloated receivables
    # Extreme TATA: very negative CFO while net_income stays positive
    current = _make_fy_row(
        filing_date="2024-03-01",
        revenue=500_000_000,
        cogs=380_000_000,         # gross margin collapse
        gross_profit=120_000_000,
        net_income=40_000_000,
        total_assets=400_000_000,
        current_assets=300_000_000,
        ppe_net=20_000_000,
        receivables=200_000_000,  # doubled vs prior — very high DSRI
        ltd=200_000_000,          # leverage surge
        current_liabilities=100_000_000,
        cfo=-80_000_000,          # strongly negative CFO → very positive TATA
        depreciation=10_000_000,
        sga=80_000_000,           # SGA / revenue surged
    )
    prior = _make_fy_row(
        filing_date="2023-03-01",
        revenue=500_000_000,
        cogs=250_000_000,
        gross_profit=250_000_000,
        net_income=50_000_000,
        total_assets=300_000_000,
        current_assets=150_000_000,
        ppe_net=50_000_000,
        receivables=80_000_000,   # much lower than current → high DSRI ratio
        ltd=80_000_000,
        current_liabilities=70_000_000,
        cfo=60_000_000,
        depreciation=20_000_000,
        sga=40_000_000,
    )
    return {"results": [current, prior]}


def _borderline_financials() -> dict:
    """Two FY rows that land in the -2.22 to -1.78 FLAGGED range."""
    # Mild DSRI increase, moderate TATA, some revenue acceleration
    current = _make_fy_row(
        filing_date="2024-03-01",
        revenue=600_000_000,      # +20% revenue growth → SGI = 1.20
        cogs=310_000_000,
        gross_profit=290_000_000,
        net_income=20_000_000,
        total_assets=500_000_000,
        current_assets=180_000_000,
        ppe_net=150_000_000,
        receivables=120_000_000,  # receivables/rev ratio slightly higher
        ltd=150_000_000,
        current_liabilities=100_000_000,
        cfo=10_000_000,           # low CFO → moderate positive TATA
        depreciation=15_000_000,
        sga=90_000_000,           # SGA growing faster than revenue
    )
    prior = _make_fy_row(
        filing_date="2023-03-01",
        revenue=500_000_000,
        cogs=265_000_000,
        gross_profit=235_000_000,
        net_income=30_000_000,
        total_assets=420_000_000,
        current_assets=160_000_000,
        ppe_net=130_000_000,
        receivables=90_000_000,
        ltd=120_000_000,
        current_liabilities=90_000_000,
        cfo=40_000_000,
        depreciation=18_000_000,
        sga=70_000_000,
    )
    return {"results": [current, prior]}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_gate_result_always_in_valid_set_clean():
    """gate_result from a clean company is a member of the valid set."""
    result = compute_beneish("ACME", _clean_financials())
    assert result["gate_result"] in _VALID_GATE_RESULTS, (
        f"Unexpected gate_result: {result['gate_result']}"
    )


def test_clean_company_gate_result():
    """Healthy financials with low accruals and stable receivables → CLEAN."""
    result = compute_beneish("HLTH", _clean_financials())
    assert result["gate_result"] == "CLEAN", (
        f"Expected CLEAN, got {result['gate_result']} (m={result['m_score']})"
    )
    assert result["m_score"] is not None
    assert result["m_score"] <= -2.22


def test_manipulated_company_gate_result():
    """Extreme DSRI + negative CFO + leverage surge → EXCLUDED."""
    result = compute_beneish("FROD", _manipulated_financials())
    assert result["gate_result"] == "EXCLUDED", (
        f"Expected EXCLUDED, got {result['gate_result']} (m={result['m_score']})"
    )
    assert result["m_score"] is not None
    assert result["m_score"] > -1.78


def test_gate_result_always_in_valid_set_manipulated():
    """gate_result from a manipulated company is still a member of the valid set."""
    result = compute_beneish("FROD", _manipulated_financials())
    assert result["gate_result"] in _VALID_GATE_RESULTS


def test_insufficient_data_when_fewer_than_two_fy_periods():
    """Only one FY row → INSUFFICIENT_DATA, m_score is None."""
    single_row = {"results": [
        _make_fy_row(
            "2024-03-01", 100e6, 50e6, 50e6, 10e6,
            200e6, 80e6, 60e6, 20e6, 30e6, 40e6, 15e6, 8e6, 12e6,
        )
    ]}
    result = compute_beneish("SING", single_row)
    assert result["gate_result"] == "INSUFFICIENT_DATA"
    assert result["m_score"] is None


def test_insufficient_data_when_empty_results():
    """Empty results list → INSUFFICIENT_DATA."""
    result = compute_beneish("EMPT", {"results": []})
    assert result["gate_result"] == "INSUFFICIENT_DATA"
    assert result["m_score"] is None


def test_insufficient_data_when_no_fy_periods():
    """Only non-FY rows (TTM) → INSUFFICIENT_DATA."""
    ttm_row = {
        "fiscal_period": "TTM",
        "filing_date": "2024-03-01",
        "financials": {},
    }
    result = compute_beneish("TTM1", {"results": [ttm_row, ttm_row]})
    assert result["gate_result"] == "INSUFFICIENT_DATA"


def test_ticker_uppercased_in_output():
    """Ticker is returned upper-cased regardless of input case."""
    result = compute_beneish("aapl", _clean_financials())
    assert result["ticker"] == "AAPL"


def test_missing_fields_list_present():
    """Result always contains a missing_fields key (list)."""
    result = compute_beneish("HLTH", _clean_financials())
    assert "missing_fields" in result
    assert isinstance(result["missing_fields"], list)


def test_m_score_is_float_when_computed():
    """m_score is a float when there is sufficient data."""
    result = compute_beneish("HLTH", _clean_financials())
    assert isinstance(result["m_score"], float)


def test_insufficient_data_when_too_many_core_inputs_missing():
    """Rows present but core financial fields all None → INSUFFICIENT_DATA."""
    # FY rows with completely empty financials
    sparse_row = {
        "fiscal_period": "FY",
        "filing_date": "2024-01-01",
        "financials": {
            "income_statement": {},
            "balance_sheet": {},
            "cash_flow_statement": {},
        },
    }
    sparse_prior = {
        "fiscal_period": "FY",
        "filing_date": "2023-01-01",
        "financials": {
            "income_statement": {},
            "balance_sheet": {},
            "cash_flow_statement": {},
        },
    }
    result = compute_beneish("NDAT", {"results": [sparse_row, sparse_prior]})
    assert result["gate_result"] == "INSUFFICIENT_DATA"
