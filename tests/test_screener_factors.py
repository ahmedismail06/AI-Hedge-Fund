"""
Smoke tests for factor scorers:
  - backend/screener/factors/quality.py   (score_quality)
  - backend/screener/factors/value.py     (score_value)
  - backend/screener/factors/momentum.py  (score_momentum)
  - backend/screener/factors/short_interest.py (score_short_interest — Phase 2 stub)

All tests use synthetic in-memory data — no API calls, no Supabase.

Coverage:
  score_quality:
    - Returns required keys: ticker, raw_values, sector
    - raw_values keys present: gross_margin, revenue_growth_yoy, roe, debt_to_equity, eps_beat_rate
    - gross_margin computed correctly from gross_profit / revenue
    - gross_margin computed from cogs when gross_profit absent
    - revenue_growth_yoy computed correctly
    - roe computed correctly
    - eps_beat_rate: 3/4 beats → 0.75
    - eps_beat_rate: no earningsHistory → None
    - Empty polygon_financials → all raw_values are None
    - Ticker uppercased

  score_value:
    - Returns required keys: ticker, raw_values
    - ev computed: market_cap + ltd - cash
    - ev_multiple uses EV/EBITDA for profitable companies
    - ev_multiple uses EV/Revenue for pre-profit companies
    - p_fcf computed when market_cap and ttm_cfo present
    - price_book computed when market_cap and book_value present
    - Missing market_cap → ev is None, ev_multiple is None
    - Ticker uppercased

  score_momentum:
    - Returns required keys: ticker, raw_values, short_interest_bonus
    - price_12_1 computed correctly on sufficiently long price history
    - price_6_1 computed correctly
    - Insufficient price history → None for both price metrics
    - eps_revision: next > current → positive revision
    - short_interest_bonus = 0.0 when SI ≤ 20%
    - short_interest_bonus = 0.5 when 20 < SI ≤ 30
    - short_interest_bonus = 1.0 when SI > 30
    - Missing fmp_data → eps_revision is None, si_bonus = 0.0
    - Ticker uppercased

  score_short_interest (Phase 2 stub):
    - Returns {"ticker": ..., "score": None, "active": False}
    - active is always False in Phase 1
"""

import math

from backend.screener.factors.quality import score_quality
from backend.screener.factors.value import score_value
from backend.screener.factors.momentum import score_momentum
from backend.screener.factors.short_interest import score_short_interest


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _w(v):
    """Wrap a value in Polygon's {"value": v} dict format."""
    return {"value": v}


def _make_polygon_financials(
    current_revenue=500e6,
    current_cogs=200e6,
    current_gross_profit=300e6,
    current_net_income=50e6,
    current_equity=200e6,
    current_total_debt=50e6,
    prior_revenue=400e6,
    prior_cogs=180e6,
    prior_gross_profit=220e6,
    prior_net_income=40e6,
    prior_equity=180e6,
    prior_total_debt=45e6,
    # value extras
    current_ebitda=80e6,
    current_cfo=70e6,
    current_capex=-10e6,
    current_equity_book=200e6,
    current_shares=20e6,
) -> dict:
    """Build a minimal two-FY Polygon financials dict."""
    current_row = {
        "fiscal_period": "FY",
        "filing_date": "2024-03-01",
        "financials": {
            "income_statement": {
                "revenues":                         _w(current_revenue),
                "cost_of_revenue":                  _w(current_cogs),
                "gross_profit":                     _w(current_gross_profit),
                "net_income_loss":                  _w(current_net_income),
                "earnings_before_interest_taxes_depreciation_and_amortization": _w(current_ebitda),
                "selling_general_administrative_expenses": _w(40e6),
                "depreciation_and_amortization":    _w(15e6),
            },
            "balance_sheet": {
                "equity":                               _w(current_equity),
                "long_term_debt":                       _w(current_total_debt),
                "current_portion_of_long_term_debt":    _w(5e6),
                "equity_attributable_to_parent":        _w(current_equity_book),
                "common_shares_outstanding":            _w(current_shares),
                "assets":                               _w(current_equity + current_total_debt + 50e6),
                "current_assets":                       _w(150e6),
                "fixed_assets":                         _w(80e6),
                "accounts_receivable":                  _w(60e6),
                "current_liabilities":                  _w(70e6),
            },
            "cash_flow_statement": {
                "net_cash_flow_from_operating_activities": _w(current_cfo),
                "capital_expenditure":                     _w(current_capex),
            },
        },
    }
    prior_row = {
        "fiscal_period": "FY",
        "filing_date": "2023-03-01",
        "financials": {
            "income_statement": {
                "revenues":        _w(prior_revenue),
                "cost_of_revenue": _w(prior_cogs),
                "gross_profit":    _w(prior_gross_profit),
                "net_income_loss": _w(prior_net_income),
                "earnings_before_interest_taxes_depreciation_and_amortization": _w(60e6),
                "selling_general_administrative_expenses": _w(35e6),
                "depreciation_and_amortization": _w(12e6),
            },
            "balance_sheet": {
                "equity":                            _w(prior_equity),
                "long_term_debt":                    _w(prior_total_debt),
                "current_portion_of_long_term_debt": _w(4e6),
                "assets":                            _w(prior_equity + prior_total_debt + 40e6),
                "current_assets":                    _w(130e6),
                "fixed_assets":                      _w(70e6),
                "accounts_receivable":               _w(50e6),
                "current_liabilities":               _w(60e6),
            },
            "cash_flow_statement": {
                "net_cash_flow_from_operating_activities": _w(55e6),
            },
        },
    }
    return {"results": [current_row, prior_row]}


def _make_price_history(num_months: int = 14) -> list[dict]:
    """
    Generate synthetic price history with num_months * 21 bars.
    Prices increase by 1% per bar from 100.0 (consistent upward trend).
    """
    bars = num_months * 21
    return [
        {"date": f"bar_{i}", "close": 100.0 * (1.01 ** i)}
        for i in range(bars)
    ]


# ===========================================================================
# score_quality tests
# ===========================================================================

def test_score_quality_returns_required_keys():
    """Result must have ticker, raw_values, sector."""
    result = score_quality("AAPL", _make_polygon_financials(), {})
    assert "ticker" in result
    assert "raw_values" in result
    assert "sector" in result


def test_score_quality_raw_values_has_all_sub_metrics():
    """raw_values must contain all 5 sub-metrics."""
    result = score_quality("AAPL", _make_polygon_financials(), {})
    rv = result["raw_values"]
    for key in ("gross_margin", "revenue_growth_yoy", "roe", "debt_to_equity", "eps_beat_rate"):
        assert key in rv, f"Missing sub-metric: {key}"


def test_score_quality_gross_margin_from_gross_profit():
    """gross_margin = gross_profit / revenue when gross_profit is available."""
    pf = _make_polygon_financials(current_gross_profit=300e6, current_revenue=500e6)
    result = score_quality("AAPL", pf, {})
    expected = 300e6 / 500e6
    assert math.isclose(result["raw_values"]["gross_margin"], expected, rel_tol=1e-6)


def test_score_quality_gross_margin_from_cogs_fallback():
    """gross_margin = (revenue - cogs) / revenue when gross_profit field is absent."""
    # Build a row that omits gross_profit
    row_no_gp = {
        "fiscal_period": "FY",
        "filing_date": "2024-03-01",
        "financials": {
            "income_statement": {
                "revenues":        _w(500e6),
                "cost_of_revenue": _w(200e6),
                # deliberately no gross_profit key
                "net_income_loss": _w(50e6),
                "earnings_before_interest_taxes_depreciation_and_amortization": _w(80e6),
                "selling_general_administrative_expenses": _w(40e6),
                "depreciation_and_amortization": _w(15e6),
            },
            "balance_sheet": {
                "equity": _w(200e6), "long_term_debt": _w(50e6),
                "current_portion_of_long_term_debt": _w(5e6),
            },
            "cash_flow_statement": {"net_cash_flow_from_operating_activities": _w(70e6)},
        },
    }
    prior_row = {
        "fiscal_period": "FY",
        "filing_date": "2023-03-01",
        "financials": {
            "income_statement": {
                "revenues": _w(400e6), "cost_of_revenue": _w(180e6),
                "net_income_loss": _w(40e6),
            },
            "balance_sheet": {"equity": _w(180e6), "long_term_debt": _w(45e6)},
            "cash_flow_statement": {},
        },
    }
    pf = {"results": [row_no_gp, prior_row]}
    result = score_quality("COGS", pf, {})
    expected = (500e6 - 200e6) / 500e6
    assert math.isclose(result["raw_values"]["gross_margin"], expected, rel_tol=1e-6)


def test_score_quality_revenue_growth_yoy():
    """revenue_growth_yoy = (current - prior) / |prior|."""
    pf = _make_polygon_financials(current_revenue=500e6, prior_revenue=400e6)
    result = score_quality("GRW", pf, {})
    expected = (500e6 - 400e6) / 400e6  # 0.25
    assert math.isclose(result["raw_values"]["revenue_growth_yoy"], expected, rel_tol=1e-6)


def test_score_quality_roe():
    """roe = net_income / equity."""
    pf = _make_polygon_financials(current_net_income=50e6, current_equity=200e6)
    result = score_quality("ROE1", pf, {})
    expected = 50e6 / 200e6
    assert math.isclose(result["raw_values"]["roe"], expected, rel_tol=1e-6)


def test_score_quality_eps_beat_rate_computed():
    """eps_beat_rate = beats / total quarters."""
    yf_info = {
        "earningsHistory": [
            {"epsActual": 1.0, "epsEstimate": 0.9},   # beat
            {"epsActual": 0.8, "epsEstimate": 0.9},   # miss
            {"epsActual": 1.2, "epsEstimate": 1.0},   # beat
            {"epsActual": 0.5, "epsEstimate": 0.6},   # miss
        ]
    }
    result = score_quality("EPS1", _make_polygon_financials(), yf_info)
    assert math.isclose(result["raw_values"]["eps_beat_rate"], 0.5, rel_tol=1e-6)


def test_score_quality_eps_beat_rate_three_of_four():
    """3/4 beats → beat rate 0.75."""
    yf_info = {
        "earningsHistory": [
            {"epsActual": 1.1, "epsEstimate": 1.0},  # beat
            {"epsActual": 0.9, "epsEstimate": 0.8},  # beat
            {"epsActual": 1.3, "epsEstimate": 1.2},  # beat
            {"epsActual": 0.7, "epsEstimate": 0.9},  # miss
        ]
    }
    result = score_quality("EPS2", _make_polygon_financials(), yf_info)
    assert math.isclose(result["raw_values"]["eps_beat_rate"], 0.75, rel_tol=1e-6)


def test_score_quality_eps_beat_rate_none_when_no_history():
    """eps_beat_rate is None when earningsHistory is absent."""
    result = score_quality("NOEPS", _make_polygon_financials(), {})
    assert result["raw_values"]["eps_beat_rate"] is None


def test_score_quality_empty_polygon_financials_returns_none_values():
    """Empty polygon_financials → all raw_values are None (no FY data)."""
    result = score_quality("EMPTY", {"results": []}, {})
    rv = result["raw_values"]
    for key in ("gross_margin", "revenue_growth_yoy", "roe", "debt_to_equity"):
        assert rv[key] is None, f"Expected None for {key}, got {rv[key]}"


def test_score_quality_ticker_uppercased():
    """Ticker in result is upper-cased."""
    result = score_quality("aapl", _make_polygon_financials(), {})
    assert result["ticker"] == "AAPL"


def test_score_quality_sector_from_yf_info():
    """sector passes through from yf_info."""
    result = score_quality("SECT", _make_polygon_financials(), {"sector": "Healthcare"})
    assert result["sector"] == "Healthcare"


# ===========================================================================
# score_value tests
# ===========================================================================

def _make_fmp_data(
    market_cap=1_000_000_000,
    long_term_debt=50_000_000,
    cash=100_000_000,
    ttm_operating_cash_flow=80_000_000,
    consensus_eps_current_year=2.0,
    consensus_eps_next_year=2.5,
    short_interest_pct=10.0,
) -> dict:
    return {
        "market_cap": market_cap,
        "long_term_debt": long_term_debt,
        "cash": cash,
        "ttm_operating_cash_flow": ttm_operating_cash_flow,
        "consensus_eps_current_year": consensus_eps_current_year,
        "consensus_eps_next_year": consensus_eps_next_year,
        "short_interest_pct": short_interest_pct,
    }


def test_score_value_returns_required_keys():
    """Result must have ticker and raw_values."""
    result = score_value("AAPL", _make_polygon_financials(), _make_fmp_data())
    assert "ticker" in result
    assert "raw_values" in result


def test_score_value_ev_computed_correctly():
    """EV = market_cap + LTD - cash."""
    fmp = _make_fmp_data(market_cap=1_000_000_000, long_term_debt=50_000_000, cash=100_000_000)
    result = score_value("EV01", _make_polygon_financials(), fmp)
    expected_ev = 1_000_000_000 + 50_000_000 - 100_000_000
    assert math.isclose(result["raw_values"]["ev"], expected_ev, rel_tol=1e-6)


def test_score_value_ev_multiple_uses_ebitda_for_profitable():
    """Profitable company (positive EBITDA) → ev_type = 'EV/EBITDA'."""
    pf = _make_polygon_financials(current_ebitda=80e6)
    fmp = _make_fmp_data(market_cap=1_000e6, long_term_debt=50e6, cash=100e6)
    result = score_value("PROF", pf, fmp)
    assert result["raw_values"]["ev_type"] == "EV/EBITDA"
    assert result["raw_values"]["ev_multiple"] is not None


def test_score_value_ev_multiple_uses_revenue_for_pre_profit():
    """Pre-profit company (EBITDA = None or 0) → ev_type = 'EV/Revenue'."""
    # Build polygon financials without EBITDA
    pf = _make_polygon_financials(current_ebitda=0)
    fmp = _make_fmp_data(market_cap=500e6, long_term_debt=30e6, cash=80e6)
    result = score_value("PREPRF", pf, fmp)
    assert result["raw_values"]["ev_type"] == "EV/Revenue"


def test_score_value_p_fcf_computed():
    """P/FCF = market_cap / (CFO - |capex|) when both present and FCF > 0."""
    pf = _make_polygon_financials(current_cfo=80e6, current_capex=-10e6)
    fmp = _make_fmp_data(market_cap=1_000e6, ttm_operating_cash_flow=80e6)
    result = score_value("FCF1", pf, fmp)
    # FCF = 80e6 - 10e6 = 70e6; P/FCF = 1000e6 / 70e6 ≈ 14.29
    expected = 1_000e6 / (80e6 - 10e6)
    assert result["raw_values"]["p_fcf"] is not None
    assert math.isclose(result["raw_values"]["p_fcf"], expected, rel_tol=1e-4)


def test_score_value_price_book_computed():
    """P/B = market_cap / equity (book_value)."""
    pf = _make_polygon_financials(current_equity_book=200e6)
    fmp = _make_fmp_data(market_cap=600e6)
    result = score_value("PB01", pf, fmp)
    assert result["raw_values"]["price_book"] is not None
    # price_book = market_cap / equity
    assert result["raw_values"]["price_book"] > 0


def test_score_value_missing_market_cap_produces_none_ev():
    """No market_cap → ev is None, ev_multiple is None."""
    fmp = _make_fmp_data(market_cap=None)
    result = score_value("NOMC", _make_polygon_financials(), fmp)
    assert result["raw_values"]["ev"] is None
    assert result["raw_values"]["ev_multiple"] is None


def test_score_value_ticker_uppercased():
    """Ticker in result is upper-cased."""
    result = score_value("aapl", _make_polygon_financials(), _make_fmp_data())
    assert result["ticker"] == "AAPL"


# ===========================================================================
# score_momentum tests
# ===========================================================================

def test_score_momentum_returns_required_keys():
    """Result must have ticker, raw_values, short_interest_bonus."""
    result = score_momentum("AAPL", _make_price_history(14), _make_fmp_data())
    assert "ticker" in result
    assert "raw_values" in result
    assert "short_interest_bonus" in result


def test_score_momentum_raw_values_has_all_sub_metrics():
    """raw_values must have price_12_1, price_6_1, eps_revision."""
    result = score_momentum("AAPL", _make_price_history(14), _make_fmp_data())
    rv = result["raw_values"]
    for key in ("price_12_1", "price_6_1", "eps_revision"):
        assert key in rv, f"Missing sub-metric: {key}"


def test_score_momentum_price_12_1_computed_on_long_history():
    """price_12_1 is not None when price history covers >= 13 months."""
    history = _make_price_history(14)  # 14 * 21 = 294 bars
    result = score_momentum("MOM1", history, {})
    assert result["raw_values"]["price_12_1"] is not None


def test_score_momentum_price_6_1_computed_on_long_history():
    """price_6_1 is not None when price history covers >= 7 months."""
    history = _make_price_history(14)
    result = score_momentum("MOM2", history, {})
    assert result["raw_values"]["price_6_1"] is not None


def test_score_momentum_prices_none_on_short_history():
    """price_12_1 and price_6_1 are None when price history is too short."""
    short_history = _make_price_history(2)  # only 42 bars — insufficient for 12-1
    result = score_momentum("SHORT", short_history, {})
    assert result["raw_values"]["price_12_1"] is None


def test_score_momentum_empty_price_history():
    """Empty price history → None for both price metrics."""
    result = score_momentum("NOPX", [], {})
    assert result["raw_values"]["price_12_1"] is None
    assert result["raw_values"]["price_6_1"] is None


def test_score_momentum_eps_revision_positive_when_next_year_higher():
    """EPS revision is positive when next_year EPS > current_year EPS."""
    fmp = _make_fmp_data(consensus_eps_current_year=2.0, consensus_eps_next_year=2.5)
    result = score_momentum("EPSREV", _make_price_history(14), fmp)
    # revision = (2.5 - 2.0) / 2.0 = 0.25
    assert result["raw_values"]["eps_revision"] is not None
    assert result["raw_values"]["eps_revision"] > 0


def test_score_momentum_eps_revision_negative_when_next_year_lower():
    """EPS revision is negative when next_year EPS < current_year EPS."""
    fmp = _make_fmp_data(consensus_eps_current_year=2.0, consensus_eps_next_year=1.5)
    result = score_momentum("EPSDN", _make_price_history(14), fmp)
    assert result["raw_values"]["eps_revision"] is not None
    assert result["raw_values"]["eps_revision"] < 0


def test_score_momentum_eps_revision_none_when_missing():
    """eps_revision is None when consensus_eps_current_year is absent."""
    result = score_momentum("NOEPS", _make_price_history(14), {})
    assert result["raw_values"]["eps_revision"] is None


def test_score_momentum_si_bonus_zero_when_si_low():
    """short_interest_bonus = 0.0 when SI ≤ 20%."""
    fmp = _make_fmp_data(short_interest_pct=15.0)
    result = score_momentum("SI_LOW", _make_price_history(14), fmp)
    assert result["short_interest_bonus"] == 0.0


def test_score_momentum_si_bonus_half_when_si_between_20_and_30():
    """short_interest_bonus = 0.5 when 20 < SI ≤ 30."""
    fmp = _make_fmp_data(short_interest_pct=25.0)
    result = score_momentum("SI_MID", _make_price_history(14), fmp)
    assert result["short_interest_bonus"] == 0.5


def test_score_momentum_si_bonus_one_when_si_above_30():
    """short_interest_bonus = 1.0 when SI > 30."""
    fmp = _make_fmp_data(short_interest_pct=35.0)
    result = score_momentum("SI_HI", _make_price_history(14), fmp)
    assert result["short_interest_bonus"] == 1.0


def test_score_momentum_si_bonus_none_si_pct():
    """short_interest_bonus = 0.0 when short_interest_pct is absent."""
    result = score_momentum("NOSI", _make_price_history(14), {})
    assert result["short_interest_bonus"] == 0.0


def test_score_momentum_ticker_uppercased():
    """Ticker in result is upper-cased."""
    result = score_momentum("aapl", _make_price_history(14), {})
    assert result["ticker"] == "AAPL"


# ===========================================================================
# score_short_interest (Phase 2 stub) tests
# ===========================================================================

def test_short_interest_stub_returns_required_keys():
    """Stub returns ticker, score, and active keys."""
    result = score_short_interest("AAPL", {"short_interest_pct": 35.0})
    assert "ticker" in result
    assert "score" in result
    assert "active" in result


def test_short_interest_stub_score_is_none():
    """score is always None in Phase 1 stub."""
    result = score_short_interest("TSLA", {"short_interest_pct": 50.0})
    assert result["score"] is None


def test_short_interest_stub_active_is_false():
    """active is always False in Phase 1 stub."""
    result = score_short_interest("GME", {"short_interest_pct": 60.0})
    assert result["active"] is False


def test_short_interest_stub_ticker_uppercased():
    """Ticker in result is upper-cased."""
    result = score_short_interest("aapl", {})
    assert result["ticker"] == "AAPL"


# ===========================================================================
# Edge-case additions
# ===========================================================================

# ---------------------------------------------------------------------------
# score_quality edge cases
# ---------------------------------------------------------------------------

def test_score_quality_zero_revenue_returns_none_gross_margin():
    """Revenue = 0 → division guard fires → gross_margin is None."""
    pf = _make_polygon_financials(current_revenue=0, current_gross_profit=0, current_cogs=0)
    result = score_quality("ZERO", pf, {})
    assert result["raw_values"]["gross_margin"] is None


def test_score_quality_eps_beat_rate_exact_tie_is_not_a_beat():
    """
    epsActual == epsEstimate must NOT count as a beat (condition is strictly >).
    One tie + one genuine beat → beat_rate = 0.5, not 1.0.
    """
    yf_info = {
        "earningsHistory": [
            {"epsActual": 1.0, "epsEstimate": 1.0},   # tie → miss
            {"epsActual": 1.2, "epsEstimate": 1.0},   # genuine beat
        ]
    }
    pf = _make_polygon_financials()
    result = score_quality("TIE", pf, yf_info)
    beat_rate = result["raw_values"]["eps_beat_rate"]
    # 1 beat out of 2 quarters
    assert beat_rate is not None
    assert math.isclose(beat_rate, 0.5, rel_tol=1e-6)


# ---------------------------------------------------------------------------
# score_value edge cases
# ---------------------------------------------------------------------------

def test_score_value_negative_fcf_returns_none_p_fcf():
    """CFO=5, capex=50 → FCF = 5 - 50 = -45 (negative) → p_fcf is None."""
    pf = _make_polygon_financials(current_cfo=5e6, current_capex=-50e6)
    fmp = {
        "market_cap": 500e6,
        "ltd": 50e6,
        "cash": 20e6,
        "ttm_cfo": 5e6,
        "capex": -50e6,
    }
    result = score_value("NEG", pf, fmp)
    assert result["raw_values"]["p_fcf"] is None


def test_score_value_negative_book_value_returns_none_price_book():
    """Negative book value (equity) → price_book guard fires → None."""
    pf = _make_polygon_financials(current_equity=-30e6)
    fmp = {
        "market_cap": 200e6,
        "ltd": 10e6,
        "cash": 5e6,
        "ttm_cfo": 20e6,
        "capex": -5e6,
    }
    result = score_value("NEGBK", pf, fmp)
    assert result["raw_values"]["price_book"] is None


# ---------------------------------------------------------------------------
# score_momentum edge cases
# ---------------------------------------------------------------------------

def test_score_momentum_si_at_exactly_20_gives_no_bonus():
    """short_interest_pct=20.0 — condition is strictly > 20 → bonus = 0.0."""
    fmp = {
        "short_interest_pct": 20.0,
        "days_to_cover": 3.0,
        "consensus_eps_current_year": 2.0,
        "consensus_eps_next_year": 2.2,
    }
    result = score_momentum("EXACT", _make_price_history(14), fmp)
    assert result["short_interest_bonus"] == 0.0


def test_score_momentum_si_at_exactly_30_gives_half_bonus():
    """short_interest_pct=30.0 — condition is strictly > 30 for 1.0 → bonus = 0.5."""
    fmp = {
        "short_interest_pct": 30.0,
        "days_to_cover": 5.0,
        "consensus_eps_current_year": 2.0,
        "consensus_eps_next_year": 2.2,
    }
    result = score_momentum("BOUND", _make_price_history(14), fmp)
    assert result["short_interest_bonus"] == 0.5
