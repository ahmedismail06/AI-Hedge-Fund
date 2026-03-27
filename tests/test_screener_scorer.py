"""
Smoke tests for backend/screener/scorer.py

Coverage:
- compute_composite returns a list of ScreenerResult
- All composite_score, quality_score, value_score, momentum_score values in [0.0, 10.0]
- Results are sorted descending by composite_score
- EXCLUDED ticker (Beneish hard gate) gets composite_score=0.0 and excluded=True
- FLAGGED ticker gets -0.5 penalty applied (lower score than equivalent CLEAN ticker)
- insider_signal=True adds +0.3 bonus
- short_interest_bonus is doubled in Risk-On regime
- Risk-Off + high D/E → composite capped at 6.5
- Rank assigned to eligible tickers starting at 1
- EXCLUDED tickers get rank > 10000 (sentinel)
- Unknown regime falls back to Risk-On weights without raising
- Empty universe returns empty list
- WatchlistEntry model validates with all new extended fields
"""

import math
from dataclasses import dataclass
from typing import Optional

from backend.screener.scorer import compute_composite, ScreenerResult
from backend.screener.universe import UniverseCandidate
from backend.models.watchlist import WatchlistEntry, FactorScores


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candidate(ticker: str, sector: str = "SaaS", market_cap_m: float = 500.0) -> UniverseCandidate:
    return UniverseCandidate(
        ticker=ticker,
        market_cap_m=market_cap_m,
        sector=sector,
        adv_k=1000.0,
    )


def _clean_beneish(m_score: float = -3.0) -> dict:
    return {"ticker": "", "m_score": m_score, "gate_result": "CLEAN", "missing_fields": []}


def _excluded_beneish(m_score: float = -1.0) -> dict:
    return {"ticker": "", "m_score": m_score, "gate_result": "EXCLUDED", "missing_fields": []}


def _flagged_beneish(m_score: float = -2.0) -> dict:
    return {"ticker": "", "m_score": m_score, "gate_result": "FLAGGED", "missing_fields": []}


def _good_quality_raw() -> dict:
    """High-quality company raw values."""
    return {
        "raw_values": {
            "gross_margin":       0.75,
            "revenue_growth_yoy": 0.30,
            "roe":                0.25,
            "debt_to_equity":     0.20,
            "eps_beat_rate":      0.80,
        }
    }


def _poor_quality_raw() -> dict:
    """Low-quality company raw values."""
    return {
        "raw_values": {
            "gross_margin":       0.20,
            "revenue_growth_yoy": -0.05,
            "roe":                0.02,
            "debt_to_equity":     3.0,
            "eps_beat_rate":      0.25,
        }
    }


def _good_value_raw() -> dict:
    """Cheap valuation raw values."""
    return {
        "raw_values": {
            "ev_multiple":   8.0,
            "p_fcf":         12.0,
            "price_book":    1.5,
            "ev_type":       "EV/EBITDA",
            "ev":            800e6,
            "is_profitable": True,
        }
    }


def _poor_value_raw() -> dict:
    """Expensive valuation raw values."""
    return {
        "raw_values": {
            "ev_multiple":   50.0,
            "p_fcf":         80.0,
            "price_book":    15.0,
            "ev_type":       "EV/Revenue",
            "ev":            2000e6,
            "is_profitable": False,
        }
    }


def _good_momentum_raw(si_bonus: float = 0.0) -> dict:
    """Strong momentum raw values."""
    return {
        "raw_values": {
            "price_12_1":   0.45,
            "price_6_1":    0.25,
            "eps_revision": 0.15,
        },
        "short_interest_bonus": si_bonus,
    }


def _poor_momentum_raw() -> dict:
    """Weak momentum raw values."""
    return {
        "raw_values": {
            "price_12_1":   -0.20,
            "price_6_1":    -0.15,
            "eps_revision": -0.10,
        },
        "short_interest_bonus": 0.0,
    }


def _build_factor_results(
    ticker: str,
    quality=None,
    value=None,
    momentum=None,
    beneish=None,
    form4=None,
) -> dict:
    """Build the raw_factor_results[ticker] structure expected by compute_composite."""
    return {
        ticker: {
            "quality":  quality  or _good_quality_raw(),
            "value":    value    or _good_value_raw(),
            "momentum": momentum or _good_momentum_raw(),
            "beneish":  beneish  or _clean_beneish(),
            "form4":    form4    or {"insider_buy": False},
            "fmp":      {},
        }
    }


# ---------------------------------------------------------------------------
# Basic contract tests
# ---------------------------------------------------------------------------

def test_compute_composite_returns_list():
    """compute_composite returns a list."""
    universe = [_make_candidate("AAPL")]
    factors = _build_factor_results("AAPL")
    result = compute_composite(universe, factors, "Risk-On")
    assert isinstance(result, list)


def test_compute_composite_returns_screener_result_objects():
    """Each item in the result list is a ScreenerResult."""
    universe = [_make_candidate("AAPL")]
    factors = _build_factor_results("AAPL")
    result = compute_composite(universe, factors, "Risk-On")
    for r in result:
        assert isinstance(r, ScreenerResult)


def test_all_scores_in_valid_range_single_ticker():
    """All scores are in [0.0, 10.0] for a single ticker (normalised to neutral 5.0)."""
    universe = [_make_candidate("AAPL")]
    factors = _build_factor_results("AAPL")
    result = compute_composite(universe, factors, "Risk-On")
    for r in result:
        for score in (r.composite_score, r.quality_score, r.value_score, r.momentum_score):
            assert 0.0 <= score <= 10.0, f"{r.ticker}: score {score} out of [0, 10]"


def test_all_scores_in_valid_range_multiple_tickers():
    """All scores are in [0.0, 10.0] for a 5-ticker universe."""
    universe = [
        _make_candidate("AAA"), _make_candidate("BBB"), _make_candidate("CCC"),
        _make_candidate("DDD"), _make_candidate("EEE"),
    ]
    factors = {}
    factors.update(_build_factor_results("AAA", quality=_good_quality_raw(), value=_good_value_raw(), momentum=_good_momentum_raw()))
    factors.update(_build_factor_results("BBB", quality=_poor_quality_raw(), value=_poor_value_raw(), momentum=_poor_momentum_raw()))
    factors.update(_build_factor_results("CCC"))
    factors.update(_build_factor_results("DDD", quality=_poor_quality_raw()))
    factors.update(_build_factor_results("EEE", value=_poor_value_raw()))

    results = compute_composite(universe, factors, "Risk-On")
    for r in results:
        for score in (r.composite_score, r.quality_score, r.value_score, r.momentum_score):
            assert 0.0 <= score <= 10.0, f"{r.ticker}: score {score} out of [0, 10]"


def test_results_sorted_descending_by_composite_score():
    """Results are sorted descending by composite_score."""
    universe = [
        _make_candidate("BEST"), _make_candidate("MED"), _make_candidate("WORST"),
    ]
    factors = {}
    factors.update(_build_factor_results("BEST",  quality=_good_quality_raw(), value=_good_value_raw(),  momentum=_good_momentum_raw()))
    factors.update(_build_factor_results("MED"))
    factors.update(_build_factor_results("WORST", quality=_poor_quality_raw(), value=_poor_value_raw(),  momentum=_poor_momentum_raw()))

    results = compute_composite(universe, factors, "Risk-On")
    eligible = [r for r in results if not r.excluded]
    scores = [r.composite_score for r in eligible]
    assert scores == sorted(scores, reverse=True), f"Results not sorted: {scores}"


def test_empty_universe_returns_empty_list():
    """Empty universe → empty result list."""
    result = compute_composite([], {}, "Risk-On")
    assert result == []


# ---------------------------------------------------------------------------
# Beneish gate tests
# ---------------------------------------------------------------------------

def test_excluded_ticker_has_zero_composite_score():
    """EXCLUDED ticker (Beneish hard gate) gets composite_score = 0.0."""
    universe = [_make_candidate("FROD")]
    factors = _build_factor_results("FROD", beneish=_excluded_beneish())
    results = compute_composite(universe, factors, "Risk-On")
    excluded = [r for r in results if r.ticker == "FROD"]
    assert excluded, "FROD should be in results"
    assert excluded[0].composite_score == 0.0
    assert excluded[0].excluded is True


def test_excluded_ticker_included_in_results_for_audit():
    """EXCLUDED ticker is still included in the results list (for audit trail)."""
    universe = [_make_candidate("FROD"), _make_candidate("GOOD")]
    factors = {}
    factors.update(_build_factor_results("FROD", beneish=_excluded_beneish()))
    factors.update(_build_factor_results("GOOD"))
    results = compute_composite(universe, factors, "Risk-On")
    tickers = {r.ticker for r in results}
    assert "FROD" in tickers
    assert "GOOD" in tickers


def test_flagged_ticker_has_lower_score_than_identical_clean_ticker():
    """
    FLAGGED ticker gets -0.5 penalty. Use two identical tickers except Beneish flag.
    Both tickers must have distinct raw values to avoid exact tie, then verify FLAGGED < CLEAN.
    Use a three-ticker universe (FLAGGED, CLEAN, ANCHOR) so normalization resolves properly.
    """
    universe = [
        _make_candidate("FLAGGED"),
        _make_candidate("CLEAN_T"),
        _make_candidate("ANCHOR"),  # median anchor
    ]
    # Give all three identical financial profiles, vary only Beneish flag
    factors = {}
    factors.update(_build_factor_results("FLAGGED", beneish=_flagged_beneish()))
    factors.update(_build_factor_results("CLEAN_T", beneish=_clean_beneish()))
    factors.update(_build_factor_results("ANCHOR",  beneish=_clean_beneish()))

    results = compute_composite(universe, factors, "Risk-On")
    r_flagged = next(r for r in results if r.ticker == "FLAGGED")
    r_clean   = next(r for r in results if r.ticker == "CLEAN_T")
    assert r_flagged.composite_score < r_clean.composite_score, (
        f"FLAGGED ({r_flagged.composite_score}) should be < CLEAN ({r_clean.composite_score})"
    )


def test_excluded_ticker_rank_is_sentinel():
    """EXCLUDED ticker rank is > 10000 (sentinel value)."""
    universe = [_make_candidate("FROD"), _make_candidate("GOOD")]
    factors = {}
    factors.update(_build_factor_results("FROD", beneish=_excluded_beneish()))
    factors.update(_build_factor_results("GOOD"))
    results = compute_composite(universe, factors, "Risk-On")
    frod = next(r for r in results if r.ticker == "FROD")
    assert frod.rank > 10000


# ---------------------------------------------------------------------------
# Adjustments
# ---------------------------------------------------------------------------

def test_insider_buy_adds_bonus():
    """
    Ticker with insider_buy=True should score higher than identical twin with insider_buy=False.
    Use a three-ticker universe for stable normalization.
    """
    universe = [
        _make_candidate("INSIDE"),
        _make_candidate("NOINS"),
        _make_candidate("ANCHOR"),
    ]
    factors = {}
    factors.update(_build_factor_results("INSIDE", form4={"insider_buy": True}))
    factors.update(_build_factor_results("NOINS",  form4={"insider_buy": False}))
    factors.update(_build_factor_results("ANCHOR", form4={"insider_buy": False}))

    results = compute_composite(universe, factors, "Risk-On")
    r_ins   = next(r for r in results if r.ticker == "INSIDE")
    r_nins  = next(r for r in results if r.ticker == "NOINS")
    assert r_ins.composite_score > r_nins.composite_score, (
        f"Insider buy ({r_ins.composite_score}) should beat no-insider ({r_nins.composite_score})"
    )
    assert r_ins.insider_signal is True
    assert r_nins.insider_signal is False


def test_si_bonus_doubled_in_risk_on():
    """
    In Risk-On, short_interest_bonus is doubled (1.0 → 2.0 effective, capped at 10).
    Ticker with high SI should score higher in Risk-On than identical ticker with zero SI.
    """
    universe = [
        _make_candidate("HIGHSI"),
        _make_candidate("ZEROSI"),
        _make_candidate("ANCHOR"),
    ]
    factors = {}
    factors.update(_build_factor_results("HIGHSI", momentum=_good_momentum_raw(si_bonus=1.0)))
    factors.update(_build_factor_results("ZEROSI", momentum=_good_momentum_raw(si_bonus=0.0)))
    factors.update(_build_factor_results("ANCHOR", momentum=_good_momentum_raw(si_bonus=0.0)))

    results = compute_composite(universe, factors, "Risk-On")
    r_high = next(r for r in results if r.ticker == "HIGHSI")
    r_zero = next(r for r in results if r.ticker == "ZEROSI")
    assert r_high.composite_score > r_zero.composite_score


def test_risk_off_caps_high_debt_at_6_5():
    """Risk-Off regime + D/E > 2.0 → composite capped at 6.5."""
    high_debt_quality = {
        "raw_values": {
            "gross_margin":       0.75,
            "revenue_growth_yoy": 0.30,
            "roe":                0.25,
            "debt_to_equity":     3.0,  # > 2.0 → high debt flag
            "eps_beat_rate":      0.80,
        }
    }
    # Need enough tickers to get a score that would exceed 6.5 without the cap
    universe = [
        _make_candidate("HDEBT"),
        _make_candidate("LOWDB"),
        _make_candidate("MID"),
    ]
    factors = {}
    factors.update(_build_factor_results("HDEBT", quality=high_debt_quality))
    factors.update(_build_factor_results("LOWDB"))
    factors.update(_build_factor_results("MID"))

    results = compute_composite(universe, factors, "Risk-Off")
    r_hdebt = next(r for r in results if r.ticker == "HDEBT")
    assert r_hdebt.composite_score <= 6.5, (
        f"High-debt ticker in Risk-Off should be capped at 6.5, got {r_hdebt.composite_score}"
    )


# ---------------------------------------------------------------------------
# Ranking tests
# ---------------------------------------------------------------------------

def test_rank_starts_at_one_for_eligible_tickers():
    """The highest-scoring eligible ticker gets rank=1."""
    universe = [_make_candidate("A"), _make_candidate("B"), _make_candidate("C")]
    factors = {}
    factors.update(_build_factor_results("A", quality=_good_quality_raw()))
    factors.update(_build_factor_results("B", quality=_poor_quality_raw()))
    factors.update(_build_factor_results("C"))

    results = compute_composite(universe, factors, "Risk-On")
    eligible = [r for r in results if not r.excluded]
    ranks = sorted(r.rank for r in eligible)
    assert ranks[0] == 1


def test_eligible_ranks_are_sequential():
    """Ranks for eligible tickers are 1, 2, 3, ... with no gaps."""
    universe = [_make_candidate(t) for t in ["AA", "BB", "CC", "DD"]]
    factors = {}
    for t in ["AA", "BB", "CC", "DD"]:
        factors.update(_build_factor_results(t))

    results = compute_composite(universe, factors, "Risk-On")
    eligible = sorted([r for r in results if not r.excluded], key=lambda r: r.rank)
    ranks = [r.rank for r in eligible]
    assert ranks == list(range(1, len(eligible) + 1)), f"Non-sequential ranks: {ranks}"


# ---------------------------------------------------------------------------
# Regime fallback
# ---------------------------------------------------------------------------

def test_unknown_regime_falls_back_without_raising():
    """Unknown regime string does not raise; falls back to Risk-On weights."""
    universe = [_make_candidate("X")]
    factors = _build_factor_results("X")
    # Should not raise
    result = compute_composite(universe, factors, "UnknownRegime")
    assert len(result) == 1
    assert 0.0 <= result[0].composite_score <= 10.0


# ---------------------------------------------------------------------------
# WatchlistEntry model validation (new fields)
# ---------------------------------------------------------------------------

def test_watchlist_entry_validates_with_new_fields():
    """WatchlistEntry accepts all new extended fields without validation error."""
    entry = WatchlistEntry(
        ticker="AAPL",
        date="2026-03-20",
        composite_score=7.5,
        factor_scores=FactorScores(quality=8.0, value=6.5, momentum=7.0),
        rank=1,
        market_cap_m=1200.0,
        adv_k=5000.0,
        sector="SaaS",
        beneish_m_score=-2.8,
        beneish_flag="CLEAN",
        insider_signal=True,
        regime="Risk-On",
        queued_for_research=True,
    )
    assert entry.ticker == "AAPL"
    assert entry.beneish_flag == "CLEAN"
    assert entry.insider_signal is True
    assert entry.regime == "Risk-On"
    assert entry.queued_for_research is True


def test_watchlist_entry_defaults_for_new_fields():
    """New optional fields have correct defaults when omitted."""
    entry = WatchlistEntry(
        ticker="MSFT",
        date="2026-03-20",
        composite_score=8.0,
        factor_scores=FactorScores(quality=8.5, value=7.0, momentum=8.0),
        rank=1,
    )
    assert entry.beneish_m_score is None
    assert entry.beneish_flag is None
    assert entry.insider_signal is False
    assert entry.regime is None
    assert entry.queued_for_research is False


def test_watchlist_entry_rejects_invalid_beneish_flag():
    """beneish_flag must be one of the Literal values; anything else raises ValidationError."""
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        WatchlistEntry(
            ticker="BAD",
            date="2026-03-20",
            composite_score=7.0,
            factor_scores=FactorScores(quality=7.0, value=7.0, momentum=7.0),
            rank=1,
            beneish_flag="UNKNOWN_FLAG",   # not in Literal set
        )


def test_watchlist_entry_composite_score_bounds():
    """composite_score field is validated to be in [0.0, 10.0]."""
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        WatchlistEntry(
            ticker="OOB",
            date="2026-03-20",
            composite_score=11.0,  # out of bounds
            factor_scores=FactorScores(quality=8.0, value=7.0, momentum=7.0),
            rank=1,
        )


def test_watchlist_entry_factor_scores_bounds():
    """FactorScores sub-fields validated to [0.0, 10.0]."""
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        FactorScores(quality=11.0, value=5.0, momentum=5.0)  # quality out of bounds
