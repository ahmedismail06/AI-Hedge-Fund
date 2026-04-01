"""
Smoke tests for backend/macro/scorer.py

Coverage:
- score_indicators: Risk-On, Risk-Off, Stagflation, Transitional regime cases
- classify_regime: priority ordering (Risk-Off beats Stagflation when both could apply)
- build_indicator_scores: list contract, required keys, valid signal values
- build_raw_indicators: assembles RawIndicators from FredBlock + MarketBlock mocks;
  jobless claims multiplied by 1000
- compute_regime_score: result in [0.0, 100.0]
- compute_regime_confidence: result in [0.0, 10.0]
- Edge cases: all-None RawIndicators → valid DimensionalScores with regime == "Transitional"
"""

from backend.macro.scorer import (
    RawIndicators,
    DimensionalScores,
    score_indicators,
    classify_regime,
    build_indicator_scores,
    build_raw_indicators,
    compute_regime_score,
    compute_regime_confidence,
)
from backend.macro.indicators.fred_fetcher import FredBlock
from backend.macro.indicators.market_fetcher import MarketBlock


# ---------------------------------------------------------------------------
# Helpers: canonical indicator sets per regime
# ---------------------------------------------------------------------------

def _risk_on_ind() -> RawIndicators:
    """Strong growth, low inflation, low stress → Risk-On."""
    return RawIndicators(
        gdp_yoy=3.0,
        ism_svc=57,
        jobless_claims=210_000,
        payrolls_level=158_000,
        payrolls_mom_pct=0.14,
        cpi_yoy=2.1,
        core_cpi_yoy=2.0,
        ppi_yoy=1.5,
        pce_yoy=2.1,
        breakeven_5y=2.2,
        rate_direction=0.5,
        yield_curve_spread=120.0,
        hy_spread=240.0,
        vix=13.0,
        dxy=98.0,
        spx_pct_above_sma=5.0,
    )


def _risk_off_ind() -> RawIndicators:
    """Contracting growth, high stress, tight Fed → Risk-Off."""
    return RawIndicators(
        gdp_yoy=-0.5,
        ism_svc=47,
        jobless_claims=350_000,
        payrolls_level=158_000,
        payrolls_mom_pct=-0.05,
        cpi_yoy=3.5,
        core_cpi_yoy=3.2,
        ppi_yoy=4.0,
        pce_yoy=3.0,
        breakeven_5y=2.8,
        rate_direction=-1.0,
        yield_curve_spread=-80.0,
        hy_spread=700.0,
        vix=38.0,
        dxy=110.0,
        spx_pct_above_sma=-8.0,
    )


def _stagflation_ind() -> RawIndicators:
    """Contracting growth + high inflation → Stagflation.

    Design constraints so that Risk-Off does NOT fire first:
      - stress must be < 0.4   (vix=28→0.5 w=1.0, hy=380→0.0 w=1.0, dxy=105→0.0 w=0.5,
                               spx=-3→0.5 w=0.5 → weighted avg = (0.5+0.0+0.0+0.25)/3.0 = 0.25)
      - NOT (growth < -0.3 AND fed < 0): use rate_direction=0.5 so fed > 0
    Stagflation fires when: growth < 0 AND inflation > 0.6
      - growth: gdp=-0.8→-0.5, ism_svc=48→-0.5, payrolls 31.6K→-0.5, jobless 270K→0.0
                avg = -0.375 (< 0)
      - inflation: all metrics above 5% → all signals = 1.0, avg = 1.0 (> 0.6)
    """
    return RawIndicators(
        gdp_yoy=-0.8,
        ism_svc=48,
        jobless_claims=270_000,
        payrolls_level=158_000,
        payrolls_mom_pct=0.02,
        cpi_yoy=6.5,
        core_cpi_yoy=5.8,
        ppi_yoy=7.2,
        pce_yoy=5.5,
        breakeven_5y=3.4,
        rate_direction=0.5,    # accommodative — keeps fed > 0, preventing Risk-Off trigger
        yield_curve_spread=20.0,
        hy_spread=380.0,
        vix=28.0,
        dxy=105.0,
        spx_pct_above_sma=-3.0,
    )


def _transitional_ind() -> RawIndicators:
    """Near-neutral values — should not satisfy any strong regime condition."""
    # growth_score: gdp_yoy=0.5 → signal 0.0; ism avg ~51 → 0.0; payrolls ~0.0; jobless ~0.0
    # Avg growth ≈ 0.0  (not > 0 for Risk-On)
    # inflation: cpi ~2.3, core ~2.1, ppi ~2.0, pce ~2.3, breakeven 2.3 → all near 0.0
    # stress: vix=18→0.5 (w=1.0), hy=350→0.0 (w=1.0), dxy=102→0.0 (w=0.5), spx=-1→0.0 (w=0.5)
    #         weighted = 0.5/3.0 ≈ 0.167
    # NOT Risk-On (growth == 0.0, not > 0)
    # NOT Risk-Off (stress ≈ 0.167, not > 0.4; growth ~0.0, not < -0.3)
    # NOT Stagflation (growth ~0.0, not < 0; inflation ~0.0, not > 0.6)
    # → Transitional
    return RawIndicators(
        gdp_yoy=0.5,
        ism_svc=51,
        jobless_claims=250_000,
        payrolls_level=158_000,
        payrolls_mom_pct=0.06,
        cpi_yoy=2.3,
        core_cpi_yoy=2.1,
        ppi_yoy=2.0,
        pce_yoy=2.3,
        breakeven_5y=2.3,
        rate_direction=0.0,
        yield_curve_spread=30.0,
        hy_spread=350.0,
        vix=18.0,
        dxy=102.0,
        spx_pct_above_sma=-1.0,
    )


# ===========================================================================
# score_indicators — regime classification
# ===========================================================================

def test_score_indicators_risk_on_regime():
    """Strong growth + low inflation + low stress → regime == 'Risk-On'."""
    result = score_indicators(_risk_on_ind(), fed_tone=0.0)
    assert isinstance(result, DimensionalScores)
    assert result.regime == "Risk-On"


def test_score_indicators_risk_on_growth_score_positive():
    """Risk-On case → growth_score > 0."""
    result = score_indicators(_risk_on_ind(), fed_tone=0.0)
    assert result.growth_score > 0, f"Expected growth_score > 0, got {result.growth_score}"


def test_score_indicators_risk_on_inflation_score_low():
    """Risk-On case → inflation_score < 0.5 (benign inflation)."""
    result = score_indicators(_risk_on_ind(), fed_tone=0.0)
    assert result.inflation_score < 0.5, (
        f"Expected inflation_score < 0.5, got {result.inflation_score}"
    )


def test_score_indicators_risk_on_stress_score_low():
    """Risk-On case → stress_score < 0.3 (calm market)."""
    result = score_indicators(_risk_on_ind(), fed_tone=0.0)
    assert result.stress_score < 0.3, (
        f"Expected stress_score < 0.3, got {result.stress_score}"
    )


def test_score_indicators_risk_off_regime():
    """Contracting economy + high stress → regime == 'Risk-Off'."""
    result = score_indicators(_risk_off_ind(), fed_tone=0.0)
    assert result.regime == "Risk-Off"


def test_score_indicators_risk_off_stress_score_high():
    """Risk-Off case → stress_score > 0.5."""
    result = score_indicators(_risk_off_ind(), fed_tone=0.0)
    assert result.stress_score > 0.5, (
        f"Expected stress_score > 0.5, got {result.stress_score}"
    )


def test_score_indicators_stagflation_regime():
    """Contracting growth + high inflation + moderate stress → regime == 'Stagflation'."""
    result = score_indicators(_stagflation_ind(), fed_tone=0.0)
    assert result.regime == "Stagflation"


def test_score_indicators_stagflation_growth_score_negative():
    """Stagflation case → growth_score < 0."""
    result = score_indicators(_stagflation_ind(), fed_tone=0.0)
    assert result.growth_score < 0, (
        f"Expected growth_score < 0, got {result.growth_score}"
    )


def test_score_indicators_stagflation_inflation_score_high():
    """Stagflation case → inflation_score > 0.6."""
    result = score_indicators(_stagflation_ind(), fed_tone=0.0)
    assert result.inflation_score > 0.6, (
        f"Expected inflation_score > 0.6, got {result.inflation_score}"
    )


def test_score_indicators_transitional_regime():
    """Near-neutral values → regime == 'Transitional'."""
    result = score_indicators(_transitional_ind(), fed_tone=0.0)
    assert result.regime == "Transitional"


# ===========================================================================
# classify_regime — priority ordering
# ===========================================================================

def test_classify_regime_risk_off_takes_priority_over_stagflation():
    """
    When both Risk-Off and Stagflation conditions could apply, Risk-Off wins.
    stress=0.8 (> 0.6) qualifies for Risk-Off.
    growth=-0.5 (< 0) and inflation=0.7 (> 0.6) also qualify for Stagflation.
    Verify: Risk-Off is returned, not Stagflation.
    """
    regime = classify_regime(growth=-0.5, inflation=0.7, fed=-0.3, stress=0.8)
    assert regime == "Risk-Off", (
        f"Expected 'Risk-Off' (not Stagflation), got '{regime}'"
    )


def test_classify_regime_risk_on():
    """growth > 0, inflation < 0.5, stress < 0.3 → Risk-On."""
    regime = classify_regime(growth=0.8, inflation=0.1, fed=0.5, stress=0.1)
    assert regime == "Risk-On"


def test_classify_regime_stagflation_when_stress_not_dominant():
    """growth < 0, inflation > 0.6, stress below Risk-Off threshold → Stagflation.

    fed must be >= 0 to prevent the Risk-Off branch (growth < -0.3 AND fed < 0).
    """
    regime = classify_regime(growth=-0.4, inflation=0.8, fed=0.2, stress=0.4)
    assert regime == "Stagflation"


def test_classify_regime_transitional_fallthrough():
    """No strong signal in any dimension → Transitional."""
    regime = classify_regime(growth=0.0, inflation=0.2, fed=0.0, stress=0.1)
    assert regime == "Transitional"


# ===========================================================================
# build_indicator_scores
# ===========================================================================

def test_build_indicator_scores_returns_list():
    """build_indicator_scores returns a list."""
    result = build_indicator_scores(_risk_on_ind())
    assert isinstance(result, list)


def test_build_indicator_scores_all_items_have_required_keys():
    """Every item in the list has 'name', 'value', 'signal' keys."""
    result = build_indicator_scores(_risk_on_ind())
    for item in result:
        assert "name" in item, f"Missing 'name' in {item}"
        assert "value" in item, f"Missing 'value' in {item}"
        assert "signal" in item, f"Missing 'signal' in {item}"


def test_build_indicator_scores_signal_values_are_valid():
    """'signal' value in each item is one of {'bullish', 'neutral', 'bearish'}."""
    valid_signals = {"bullish", "neutral", "bearish"}
    result = build_indicator_scores(_risk_on_ind())
    for item in result:
        assert item["signal"] in valid_signals, (
            f"Invalid signal '{item['signal']}' for indicator '{item.get('name')}'"
        )


def test_build_indicator_scores_empty_when_all_none():
    """All-None RawIndicators → empty list (no indicators to score)."""
    ind = RawIndicators()
    result = build_indicator_scores(ind)
    assert result == []


def test_build_indicator_scores_risk_off_has_multiple_entries():
    """Risk-Off indicators produce multiple entries (>= 5 indicators present)."""
    result = build_indicator_scores(_risk_off_ind())
    assert len(result) >= 5


# ===========================================================================
# build_raw_indicators
# ===========================================================================

def _make_fred_block(
    jobless_thousands: float = 220_000.0,  # actual headcount, not thousands (FRED ICSA is actual)
    gdp_yoy: float = 2.5,
    ism_svc: float = 55.0,
    payrolls: float = 158_000.0,
    payrolls_mom_pct: float = 0.12,
    cpi_yoy: float = 2.5,
    core_cpi_yoy: float = 2.2,
    ppi_yoy: float = 2.0,
    pce_yoy: float = 2.3,
    breakeven_5y: float = 2.4,
    hy_spread: float = 300.0,
    rate_direction: float = 0.0,
    yield_curve_spread_bps: float = 50.0,
) -> FredBlock:
    # payrolls_level comes from raw_values["payrolls"]
    # payrolls_mom_pct comes from mom_changes["payrolls"]
    # gdp/cpi/core_cpi/ppi/pce come from yoy_changes
    return FredBlock(
        raw_values={
            "jobless": jobless_thousands,
            "ism_svc": ism_svc,
            "payrolls": payrolls,          # level in thousands
            "breakeven_5y": breakeven_5y,
            "hy_spread": hy_spread,
        },
        yoy_changes={
            "gdp": gdp_yoy,
            "cpi": cpi_yoy,
            "core_cpi": core_cpi_yoy,
            "ppi": ppi_yoy,
            "pce": pce_yoy,
        },
        mom_changes={"payrolls": payrolls_mom_pct},
        yield_curve_spread_bps=yield_curve_spread_bps,
        rate_direction=rate_direction,
    )


def _make_market_block(
    vix: float = 15.0,
    dxy: float = 102.0,
    spx_price: float = 4500.0,
    spx_sma_200: float = 4300.0,
    spx_pct_above_sma: float = 4.65,
) -> MarketBlock:
    return MarketBlock(
        vix=vix,
        dxy=dxy,
        spx_price=spx_price,
        spx_sma_200=spx_sma_200,
        spx_pct_above_sma=spx_pct_above_sma,
    )


def test_build_raw_indicators_returns_raw_indicators():
    """build_raw_indicators returns a RawIndicators instance."""
    fred = _make_fred_block()
    market = _make_market_block()
    result = build_raw_indicators(fred, market)
    assert isinstance(result, RawIndicators)


def test_build_raw_indicators_jobless_passed_through():
    """FRED ICSA (jobless claims) is already in actual headcount — build_raw_indicators passes it through unchanged."""
    fred = _make_fred_block(jobless_thousands=220_000.0)  # pass actual headcount, not thousands
    market = _make_market_block()
    result = build_raw_indicators(fred, market)
    assert result.jobless_claims == 220_000.0, (
        f"Expected 220000.0, got {result.jobless_claims}"
    )


def test_build_raw_indicators_vix_from_market_block():
    """VIX value comes from the MarketBlock."""
    fred = _make_fred_block()
    market = _make_market_block(vix=22.5)
    result = build_raw_indicators(fred, market)
    assert result.vix == 22.5


def test_build_raw_indicators_dxy_from_market_block():
    """DXY value comes from the MarketBlock."""
    fred = _make_fred_block()
    market = _make_market_block(dxy=105.0)
    result = build_raw_indicators(fred, market)
    assert result.dxy == 105.0


def test_build_raw_indicators_rate_direction_from_fred_block():
    """rate_direction comes from FredBlock.rate_direction."""
    fred = _make_fred_block(rate_direction=-1.0)
    market = _make_market_block()
    result = build_raw_indicators(fred, market)
    assert result.rate_direction == -1.0


def test_build_raw_indicators_yield_curve_spread_from_fred_block():
    """yield_curve_spread comes from FredBlock.yield_curve_spread_bps."""
    fred = _make_fred_block(yield_curve_spread_bps=-35.0)
    market = _make_market_block()
    result = build_raw_indicators(fred, market)
    assert result.yield_curve_spread == -35.0


def test_build_raw_indicators_none_jobless_stays_none():
    """If FRED jobless value is None, jobless_claims in RawIndicators is None (no multiply)."""
    fred = FredBlock(
        raw_values={"jobless": None},
        yoy_changes={},
        mom_changes={},
        yield_curve_spread_bps=None,
        rate_direction=0.0,
    )
    market = _make_market_block()
    result = build_raw_indicators(fred, market)
    assert result.jobless_claims is None


# ===========================================================================
# compute_regime_score
# ===========================================================================

def test_compute_regime_score_returns_float_in_0_100():
    """compute_regime_score always returns a float in [0.0, 100.0]."""
    for growth, inflation, fed, stress, regime in [
        (1.0, -1.0, 1.0, -1.0, "Risk-On"),    # best case
        (-1.0, 1.0, -1.0, 1.0, "Risk-Off"),   # worst case
        (0.0, 0.0, 0.0, 0.0, "Transitional"), # neutral
    ]:
        result = compute_regime_score(growth, inflation, fed, stress, regime)
        assert isinstance(result, float)
        assert 0.0 <= result <= 100.0, (
            f"compute_regime_score out of range: {result} for "
            f"(growth={growth}, inflation={inflation}, fed={fed}, stress={stress})"
        )


def test_compute_regime_score_risk_on_higher_than_risk_off():
    """Risk-On indicators produce a higher macro health score than Risk-Off indicators."""
    risk_on = score_indicators(_risk_on_ind(), fed_tone=0.0)
    risk_off = score_indicators(_risk_off_ind(), fed_tone=0.0)
    assert risk_on.regime_score > risk_off.regime_score


# ===========================================================================
# compute_regime_confidence
# ===========================================================================

def test_compute_regime_confidence_returns_float_in_0_10():
    """compute_regime_confidence always returns a float in [0.0, 10.0]."""
    for growth, inflation, fed, stress, regime in [
        (1.0, -1.0, 1.0, -1.0, "Risk-On"),
        (-1.0, 0.0, -1.0, 1.0, "Risk-Off"),
        (-1.0, 1.0, -0.5, 0.5, "Stagflation"),
        (0.0, 0.0, 0.0, 0.0, "Transitional"),
    ]:
        result = compute_regime_confidence(growth, inflation, fed, stress, regime)
        assert isinstance(result, float)
        assert 0.0 <= result <= 10.0, (
            f"compute_regime_confidence out of range: {result} for regime '{regime}'"
        )


def test_compute_regime_confidence_transitional_is_5():
    """Transitional regime always returns confidence = 5.0 (uncertain by definition)."""
    result = compute_regime_confidence(0.0, 0.1, 0.0, 0.2, "Transitional")
    assert result == 5.0


def test_compute_regime_confidence_strong_risk_on_is_high():
    """Clear Risk-On signals produce a high confidence score (>= 7.5)."""
    # All four pillars aligned: growth > 0.3, inflation < 0.3, stress < 0.2, fed > 0
    result = compute_regime_confidence(
        growth=0.8, inflation=0.1, fed=0.5, stress=0.1, regime="Risk-On"
    )
    assert result >= 7.5, f"Expected high confidence for clear Risk-On, got {result}"


# ===========================================================================
# Edge cases
# ===========================================================================

def test_score_indicators_all_none_returns_transitional():
    """All-None RawIndicators → valid DimensionalScores, regime == 'Transitional'."""
    ind = RawIndicators()  # all fields default to None / 0.0
    result = score_indicators(ind, fed_tone=0.0)
    assert isinstance(result, DimensionalScores)
    assert result.regime == "Transitional", (
        f"Expected 'Transitional' for all-None indicators, got '{result.regime}'"
    )


def test_score_indicators_all_none_scores_are_floats():
    """All-None RawIndicators → all dimensional scores are valid floats."""
    ind = RawIndicators()
    result = score_indicators(ind, fed_tone=0.0)
    for attr in ("growth_score", "inflation_score", "fed_score", "stress_score"):
        val = getattr(result, attr)
        assert isinstance(val, float), f"{attr} should be float, got {type(val)}"


def test_score_indicators_fed_tone_does_not_change_regime_when_neutral():
    """fed_tone clamped at ±1.0 does not flip the regime when all other signals are clear."""
    ind = _risk_on_ind()
    result_default = score_indicators(ind, fed_tone=0.0)
    result_hawkish = score_indicators(ind, fed_tone=-1.0)
    # Risk-On should be robust enough to survive a hawkish fed_tone overlay
    assert result_default.regime == "Risk-On"
    # fed_tone only affects fed_score, not growth/inflation/stress used for Risk-On check
    assert result_hawkish.regime == "Risk-On"


def test_score_indicators_fed_score_affected_by_fed_tone():
    """fed_tone shifts fed_score — dovish tone (+1.0) raises fed_score vs neutral."""
    ind = _transitional_ind()
    result_neutral = score_indicators(ind, fed_tone=0.0)
    result_dovish = score_indicators(ind, fed_tone=1.0)
    assert result_dovish.fed_score > result_neutral.fed_score


# ---------------------------------------------------------------------------
# Weighted stress aggregation tests (Problem 1 fix)
# ---------------------------------------------------------------------------


def test_stress_score_dxy_does_not_cancel_vix():
    """DXY weak signal (-0.5) must not fully cancel VIX elevated (0.5) under the
    weighted formula. With VIX=25 (signal=0.5, w=1.0) and DXY=98 (signal=-0.5, w=0.5):
    weighted_sum = 0.5*1.0 + (-0.5)*0.5 = 0.25; weight_sum = 1.5 → score = 0.167 > 0.0."""
    ind = RawIndicators(vix=25.0, hy_spread=350.0, dxy=98.0, spx_pct_above_sma=0.0)
    result = score_indicators(ind, fed_tone=0.0)
    assert result.stress_score > 0.0, (
        f"VIX=25/DXY=98 should not net to 0.0 stress; got {result.stress_score:.4f}"
    )


def test_stress_score_weighted_formula_spot_check():
    """Verify weighted aggregation arithmetic:
    vix=38→1.0 (w=1.0), hy=700→1.0 (w=1.0), dxy=110→0.5 (w=0.5), spx=-8→1.0 (w=0.5)
    weighted_sum = 1.0 + 1.0 + 0.25 + 0.5 = 2.75; weight_sum = 3.0 → score ≈ 0.9167."""
    ind = RawIndicators(vix=38.0, hy_spread=700.0, dxy=110.0, spx_pct_above_sma=-8.0)
    result = score_indicators(ind, fed_tone=0.0)
    assert abs(result.stress_score - 0.9167) < 0.001, (
        f"Expected weighted stress ≈ 0.9167, got {result.stress_score:.4f}"
    )


# ---------------------------------------------------------------------------
# Multi-signal mild stress Risk-Off test (Problem 2 fix)
# ---------------------------------------------------------------------------


def test_multi_signal_mild_stress_triggers_risk_off():
    """VIX=25 + HY=460 + SPX below SMA together breach stress > 0.4 → Risk-Off.
    weighted: (0.5*1.0 + 0.5*1.0 + 0.0*0.5 + 0.5*0.5) / 3.0 = 1.25/3.0 ≈ 0.417."""
    ind = RawIndicators(
        gdp_yoy=-0.2,
        vix=25.0,
        hy_spread=460.0,
        dxy=102.0,
        spx_pct_above_sma=-3.0,
        rate_direction=0.0,
    )
    result = score_indicators(ind, fed_tone=0.0)
    assert result.stress_score > 0.4, (
        f"Expected stress > 0.4 for multi-signal stress env, got {result.stress_score:.4f}"
    )
    assert result.regime == "Risk-Off", (
        f"Expected Risk-Off from multi-signal stress, got {result.regime}"
    )


# ---------------------------------------------------------------------------
# fed_score baseline test (Problem 3 — scorer layer is correct)
# ---------------------------------------------------------------------------


def test_fed_score_zero_fed_tone_positive_yield_curve():
    """rate_direction=0.0, yield_curve=+53 bps → signal 0.5, fed_tone=0.0
    → fed_score = (0.0 + 0.5 + 0.0) / 3 ≈ 0.1667."""
    ind = RawIndicators(rate_direction=0.0, yield_curve_spread=53.0)
    result = score_indicators(ind, fed_tone=0.0)
    assert abs(result.fed_score - 0.1667) < 0.001, (
        f"Expected fed_score ≈ 0.167, got {result.fed_score:.4f}"
    )
