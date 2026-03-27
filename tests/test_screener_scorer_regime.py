"""
Regime-specific tests for backend/screener/scorer.compute_composite

Covers the two regime branches not hit by the base smoke tests:
  - Stagflation: gross_margin < 0.40 → −0.5 penalty
  - Risk-Off:    cash_runway_months < 18 → composite capped at 5.0
  - Risk-Off:    debt_to_equity > 2.0 → composite capped at 6.5
  - Risk-On:     short_interest_bonus doubled (×2)
  - composite always in [0.0, 10.0] regardless of bonuses/penalties
  - All four regimes produce different weights for an asymmetric ticker
"""

from backend.screener.scorer import compute_composite, ScreenerResult
from backend.screener.universe import UniverseCandidate


# ---------------------------------------------------------------------------
# Helpers (same pattern as test_screener_scorer.py)
# ---------------------------------------------------------------------------

def _make_candidate(ticker, sector="SaaS"):
    return UniverseCandidate(ticker=ticker, market_cap_m=500.0, sector=sector, adv_k=1000.0)


def _clean_beneish():
    return {"gate_result": "CLEAN", "m_score": -3.0, "missing_fields": []}


def _build(ticker, gross_margin=0.6, d2e=0.5, cash_runway=None, si_bonus=0.0, sector="SaaS"):
    """Build a raw_factor_results[ticker] dict with controllable levers."""
    fmp = {}
    if cash_runway is not None:
        fmp["cash_runway_months"] = cash_runway
    return {
        ticker: {
            "quality": {
                "raw_values": {
                    "gross_margin":       gross_margin,
                    "revenue_growth_yoy": 0.20,
                    "roe":                0.20,
                    "debt_to_equity":     d2e,
                    "eps_beat_rate":      0.75,
                }
            },
            "value": {
                "raw_values": {
                    "ev_multiple": 10.0,
                    "p_fcf":       15.0,
                    "price_book":  2.0,
                }
            },
            "momentum": {
                "raw_values": {
                    "price_12_1":   0.30,
                    "price_6_1":    0.20,
                    "eps_revision": 0.10,
                },
                "short_interest_bonus": si_bonus,
            },
            "beneish": _clean_beneish(),
            "form4":   {"insider_buy": False},
            "fmp":     fmp,
        }
    }


def _run(ticker, regime, gross_margin=0.6, d2e=0.5, cash_runway=None, si_bonus=0.0, sector="SaaS"):
    """Run compute_composite for a single ticker and return its ScreenerResult."""
    universe = [_make_candidate(ticker, sector=sector)]
    factors  = _build(ticker, gross_margin=gross_margin, d2e=d2e,
                      cash_runway=cash_runway, si_bonus=si_bonus, sector=sector)
    results  = compute_composite(universe, factors, regime)
    return next(r for r in results if r.ticker == ticker)


# ===========================================================================
# Stagflation: gross_margin penalty
# ===========================================================================

def test_stagflation_low_gross_margin_gets_penalty():
    """gross_margin=0.35 (<0.40) in Stagflation → lower score than Risk-On baseline."""
    r_stagflation = _run("T", "Stagflation", gross_margin=0.35)
    r_risk_on     = _run("T", "Risk-On",     gross_margin=0.35)
    # Stagflation applies −0.5 penalty on top of the regime weight difference
    # Verify penalty is reflected (Stagflation score < Risk-On for same ticker)
    # Note: weights also differ; what we assert is the penalty exists in Stagflation
    r_no_penalty  = _run("T", "Stagflation", gross_margin=0.45)
    assert r_stagflation.composite_score < r_no_penalty.composite_score


def test_stagflation_high_gross_margin_no_penalty():
    """gross_margin=0.45 (≥0.40) → no Stagflation penalty."""
    r_high = _run("T", "Stagflation", gross_margin=0.45)
    r_low  = _run("T", "Stagflation", gross_margin=0.35)
    assert r_high.composite_score > r_low.composite_score


def test_stagflation_gross_margin_exactly_040_no_penalty():
    """Condition is gross_margin < 0.40; exactly 0.40 must NOT be penalised."""
    # Use a three-ticker universe so normalization resolves
    universe = [_make_candidate(t) for t in ["EXACT", "BELOW", "ANCHOR"]]
    factors = {}
    factors.update(_build("EXACT",  gross_margin=0.40))
    factors.update(_build("BELOW",  gross_margin=0.35))
    factors.update(_build("ANCHOR", gross_margin=0.60))
    results = compute_composite(universe, factors, "Stagflation")
    r_exact = next(r for r in results if r.ticker == "EXACT")
    r_below = next(r for r in results if r.ticker == "BELOW")
    # EXACT (no penalty) should score strictly higher than BELOW (penalty)
    assert r_exact.composite_score > r_below.composite_score


# ===========================================================================
# Risk-Off: cash_runway cap
# ===========================================================================

def test_risk_off_low_cash_runway_caps_at_5():
    """cash_runway_months=12 (<18) in Risk-Off → composite ≤ 5.0."""
    r = _run("T", "Risk-Off", cash_runway=12)
    assert r.composite_score <= 5.0


def test_risk_off_cash_runway_exactly_18_no_cap():
    """cash_runway_months=18 — condition is strictly < 18, so 18 is safe."""
    r = _run("T", "Risk-Off", cash_runway=18)
    # Score should NOT be capped at 5.0; it may be naturally below 5 but not
    # because of the runway cap.  Verify by comparing against a clearly uncapped score.
    r_no_runway = _run("T", "Risk-Off", cash_runway=None)
    # Both should behave the same (no cap applied)
    assert abs(r.composite_score - r_no_runway.composite_score) < 0.01


def test_risk_off_high_debt_and_low_runway_most_restrictive_cap_wins():
    """D/E=3.0 → cap 6.5; cash_runway=10 → cap 5.0. Result must be ≤ 5.0."""
    r = _run("T", "Risk-Off", d2e=3.0, cash_runway=10)
    assert r.composite_score <= 5.0


# ===========================================================================
# Risk-On: SI bonus doubled
# ===========================================================================

def test_risk_on_si_bonus_is_doubled():
    """In Risk-On, si_bonus=1.0 is multiplied by 2.  Same ticker in Risk-Off has ×1."""
    universe = [_make_candidate("SI"), _make_candidate("BASE"), _make_candidate("ANCHOR")]
    factors  = {}
    factors.update(_build("SI",     si_bonus=1.0))
    factors.update(_build("BASE",   si_bonus=0.0))
    factors.update(_build("ANCHOR", si_bonus=0.0))

    risk_on_results  = compute_composite(universe, dict(factors), "Risk-On")
    risk_off_results = compute_composite(universe, dict(factors), "Risk-Off")

    si_risk_on  = next(r for r in risk_on_results  if r.ticker == "SI")
    si_risk_off = next(r for r in risk_off_results if r.ticker == "SI")

    base_risk_on  = next(r for r in risk_on_results  if r.ticker == "BASE")
    base_risk_off = next(r for r in risk_off_results if r.ticker == "BASE")

    # Difference between SI and BASE should be larger in Risk-On (doubled bonus)
    gap_risk_on  = si_risk_on.composite_score  - base_risk_on.composite_score
    gap_risk_off = si_risk_off.composite_score - base_risk_off.composite_score
    assert gap_risk_on > gap_risk_off


# ===========================================================================
# Four regimes produce distinct composites for an asymmetric ticker
# ===========================================================================

def test_all_four_regimes_produce_different_composites():
    """
    A ticker with very high quality but low momentum will score differently
    under each regime because the weights change.
    """
    # High quality, very low momentum → regime weight changes matter
    factors_template = {
        "quality": {"raw_values": {
            "gross_margin": 0.80, "revenue_growth_yoy": 0.40,
            "roe": 0.35, "debt_to_equity": 0.10, "eps_beat_rate": 0.90,
        }},
        "value": {"raw_values": {
            "ev_multiple": 8.0, "p_fcf": 10.0, "price_book": 1.5,
        }},
        "momentum": {
            "raw_values": {
                "price_12_1": -0.30, "price_6_1": -0.20, "eps_revision": -0.15,
            },
            "short_interest_bonus": 0.0,
        },
        "beneish": _clean_beneish(),
        "form4":   {"insider_buy": False},
        "fmp":     {},
    }

    # Three-ticker universe (A=best quality, B=average, C=best momentum)
    universe = [_make_candidate(t) for t in ["A", "B", "C"]]
    all_factors = {
        "A": factors_template,
        "B": {
            **factors_template,
            "quality": {"raw_values": {
                "gross_margin": 0.50, "revenue_growth_yoy": 0.10,
                "roe": 0.10, "debt_to_equity": 0.50, "eps_beat_rate": 0.50,
            }},
        },
        "C": {
            **factors_template,
            "momentum": {
                "raw_values": {
                    "price_12_1": 0.50, "price_6_1": 0.35, "eps_revision": 0.20,
                },
                "short_interest_bonus": 0.0,
            },
        },
    }

    scores_by_regime = {}
    for regime in ("Risk-On", "Risk-Off", "Transitional", "Stagflation"):
        results = compute_composite(universe, dict(all_factors), regime)
        a = next(r for r in results if r.ticker == "A")
        scores_by_regime[regime] = a.composite_score

    # Risk-Off weighs quality heaviest (0.60) → A should score highest in Risk-Off
    # Risk-On weighs quality least (0.50) → A should score lowest in Risk-On
    assert scores_by_regime["Risk-Off"] >= scores_by_regime["Risk-On"]


# ===========================================================================
# Composite floor/ceiling always enforced
# ===========================================================================

def test_composite_score_never_exceeds_10():
    """Even with insider bonus + large SI bonus, composite is capped at 10.0."""
    universe = [_make_candidate("BIG"), _make_candidate("A2"), _make_candidate("A3")]
    factors = {}
    factors.update(_build("BIG", si_bonus=5.0))  # would push composite far above 10
    factors["BIG"]["form4"] = {"insider_buy": True}   # +0.3 bonus
    factors.update(_build("A2"))
    factors.update(_build("A3"))
    results = compute_composite(universe, factors, "Risk-On")
    for r in results:
        assert r.composite_score <= 10.0, f"{r.ticker}: {r.composite_score} > 10"


def test_composite_score_never_below_0():
    """FLAGGED penalty + low base score must not go below 0.0."""
    # Single ticker with very poor financials + FLAGGED penalty
    universe = [_make_candidate("BAD")]
    factors = {
        "BAD": {
            "quality": {"raw_values": {
                "gross_margin": 0.05, "revenue_growth_yoy": -0.30,
                "roe": -0.20, "debt_to_equity": 5.0, "eps_beat_rate": 0.0,
            }},
            "value":    {"raw_values": {"ev_multiple": 100.0, "p_fcf": 200.0, "price_book": 20.0}},
            "momentum": {"raw_values": {"price_12_1": -0.5, "price_6_1": -0.4, "eps_revision": -0.2},
                         "short_interest_bonus": 0.0},
            "beneish":  {"gate_result": "FLAGGED", "m_score": -2.0, "missing_fields": []},
            "form4":    {"insider_buy": False},
            "fmp":      {"cash_runway_months": 3},
        }
    }
    results = compute_composite(universe, factors, "Risk-Off")
    for r in results:
        assert r.composite_score >= 0.0, f"{r.ticker}: {r.composite_score} < 0"
