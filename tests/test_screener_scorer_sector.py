"""
Sector-relative value normalization tests for compute_composite.

Value sub-metrics (ev_multiple, p_fcf, price_book) are normalised within
sector cohorts (SaaS / Healthcare / Industrials / Unknown), not universe-wide.

Key property: a SaaS ticker with ev_multiple=50 ranks last within SaaS, but
that ranking is independent of where Healthcare tickers fall.

Tests:
  - Same-sector tickers rank independently within their cohort
  - Cross-sector isolation: two cohorts rank their own members separately
  - Single ticker in a sector gets neutral value score (n=1 guard)
  - sector=None tickers are grouped as "Unknown" and rank among each other
"""

import pytest

from backend.screener.scorer import compute_composite
from backend.screener.universe import UniverseCandidate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candidate(ticker, sector):
    return UniverseCandidate(ticker=ticker, market_cap_m=500.0, sector=sector, adv_k=1000.0)


def _clean_beneish():
    return {"gate_result": "CLEAN", "m_score": -3.0, "missing_fields": []}


def _build_ticker(ev_multiple=10.0):
    return {
        "quality": {"raw_values": {
            "gross_margin":       0.6,
            "revenue_growth_yoy": 0.20,
            "roe":                0.20,
            "debt_to_equity":     0.30,
            "eps_beat_rate":      0.70,
        }},
        "value": {"raw_values": {
            "ev_multiple": ev_multiple,
            "p_fcf":       15.0,
            "price_book":  2.0,
        }},
        "momentum": {
            "raw_values": {"price_12_1": 0.20, "price_6_1": 0.10, "eps_revision": 0.05},
            "short_interest_bonus": 0.0,
        },
        "beneish": _clean_beneish(),
        "form4":   {"insider_buy": False},
        "fmp":     {},
    }


# ===========================================================================
# Tests
# ===========================================================================

def test_same_sector_tickers_rank_internally():
    """Lowest ev_multiple → best value score within same sector."""
    universe = [_make_candidate(t, "SaaS") for t in ["CHEAP", "MED", "PRICEY"]]
    factors = {
        "CHEAP":  _build_ticker(ev_multiple=5.0),
        "MED":    _build_ticker(ev_multiple=15.0),
        "PRICEY": _build_ticker(ev_multiple=40.0),
    }
    results = compute_composite(universe, factors, "Risk-On")
    cheap  = next(r for r in results if r.ticker == "CHEAP")
    pricey = next(r for r in results if r.ticker == "PRICEY")
    assert cheap.value_score > pricey.value_score


def test_cross_sector_isolation():
    """
    SaaS and Healthcare normalise value independently.
    Same-ranked ticker (cheapest) in each sector should receive the same value score.
    """
    universe = [
        _make_candidate("SAAS_CHEAP",    "SaaS"),
        _make_candidate("SAAS_PRICEY",   "SaaS"),
        _make_candidate("HEALTH_CHEAP",  "Healthcare"),
        _make_candidate("HEALTH_PRICEY", "Healthcare"),
    ]
    factors = {
        "SAAS_CHEAP":    _build_ticker(ev_multiple=5.0),
        "SAAS_PRICEY":   _build_ticker(ev_multiple=40.0),
        "HEALTH_CHEAP":  _build_ticker(ev_multiple=5.0),
        "HEALTH_PRICEY": _build_ticker(ev_multiple=40.0),
    }
    results = compute_composite(universe, factors, "Risk-On")

    saas_cheap    = next(r for r in results if r.ticker == "SAAS_CHEAP")
    saas_pricey   = next(r for r in results if r.ticker == "SAAS_PRICEY")
    health_cheap  = next(r for r in results if r.ticker == "HEALTH_CHEAP")
    health_pricey = next(r for r in results if r.ticker == "HEALTH_PRICEY")

    # Within each sector, cheap beats pricey
    assert saas_cheap.value_score   > saas_pricey.value_score
    assert health_cheap.value_score > health_pricey.value_score

    # Same relative rank in each sector → same value score
    assert abs(saas_cheap.value_score - health_cheap.value_score) < 0.01


def test_lone_ticker_in_sector_gets_neutral_value_score():
    """
    Single ticker in its sector → len(valid)=1 in _normalize_universe → 5.0.
    """
    universe = [
        _make_candidate("LONE",  "Industrials"),
        _make_candidate("SAAS1", "SaaS"),
        _make_candidate("SAAS2", "SaaS"),
    ]
    factors = {
        "LONE":  _build_ticker(ev_multiple=10.0),
        "SAAS1": _build_ticker(ev_multiple=5.0),
        "SAAS2": _build_ticker(ev_multiple=40.0),
    }
    results = compute_composite(universe, factors, "Risk-On")
    lone = next(r for r in results if r.ticker == "LONE")
    assert lone.value_score == pytest.approx(5.0, abs=0.01)


def test_none_sector_tickers_ranked_together_as_unknown():
    """Tickers with sector=None group as 'Unknown' and rank among each other."""
    universe = [
        _make_candidate("UNK_CHEAP",  None),
        _make_candidate("UNK_PRICEY", None),
        _make_candidate("SAAS_MED",   "SaaS"),
    ]
    factors = {
        "UNK_CHEAP":  _build_ticker(ev_multiple=3.0),
        "UNK_PRICEY": _build_ticker(ev_multiple=80.0),
        "SAAS_MED":   _build_ticker(ev_multiple=15.0),
    }
    results = compute_composite(universe, factors, "Risk-On")
    unk_cheap  = next(r for r in results if r.ticker == "UNK_CHEAP")
    unk_pricey = next(r for r in results if r.ticker == "UNK_PRICEY")
    assert unk_cheap.value_score > unk_pricey.value_score
