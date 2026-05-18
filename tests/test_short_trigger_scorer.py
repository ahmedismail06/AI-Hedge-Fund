"""
Smoke tests for the short trigger-count scorer.

Covers each of the 7 triggers in isolation plus the qualification gates
(select_qualifying). Pure unit tests — no network or DB.
"""
from __future__ import annotations

import pytest

from backend.screener.short_trigger_scorer import (
    BENEISH_FLAG_THRESHOLD,
    DEBT_EBITDA_THRESHOLD,
    DILUTION_YOY_THRESHOLD,
    EV_SALES_SECTOR_MULTIPLIER,
    FCF_BURN_RUNWAY_MONTHS,
    GM_COMPRESSION_BPS,
    MIN_EVALUABLE,
    MIN_TRIGGERS,
    score_triggers,
    select_qualifying,
)


def _row(**overrides) -> dict:
    """Build a ShortUniverseRow-shaped dict with sensible defaults that fire NO triggers."""
    base = {
        "ticker": "TEST",
        "sector": "Industrials",
        "market_cap_m": 5_000.0,
        "adv_k": 10_000.0,
        "revenue_q": [110.0, 109.0, 108.0, 105.0],   # mild growth, no decel
        "gross_margin_q": [0.40, 0.40, 0.40, 0.40],
        "shares_diluted_q": [100.0, 100.0, 100.0, 99.0],
        "fcf_ttm": 50.0,
        "cash_runway_months": None,
        "cash": 200.0,
        "ev_to_sales_ttm": 2.0,
        "debt_to_ebitda_ttm": 1.0,
        "short_interest_pct": 5.0,
    }
    base.update(overrides)
    return base


# ── T1 revenue deceleration ──────────────────────────────────────────────────
def test_t1_fires_on_3_consecutive_qoq_declines():
    r = _row(revenue_q=[80.0, 90.0, 100.0, 110.0])  # falling
    out = score_triggers([r])[0]
    assert "T1_revenue_deceleration" in out.triggers_fired


def test_t1_does_not_fire_on_growth():
    r = _row(revenue_q=[120.0, 110.0, 100.0, 90.0])  # growing
    out = score_triggers([r])[0]
    assert "T1_revenue_deceleration" not in out.triggers_fired


def test_t1_evaluates_none_with_insufficient_data():
    r = _row(revenue_q=[100.0, None, None, None])
    out = score_triggers([r])[0]
    assert "T1_revenue_deceleration" not in out.triggers_evaluated


# ── T2 GM compression ────────────────────────────────────────────────────────
def test_t2_fires_on_400bp_compression():
    r = _row(gross_margin_q=[0.35, 0.38, 0.39, 0.40])  # -500bp YoY
    out = score_triggers([r])[0]
    assert "T2_gm_compression" in out.triggers_fired


def test_t2_does_not_fire_on_200bp_compression():
    r = _row(gross_margin_q=[0.38, 0.39, 0.40, 0.40])  # -200bp YoY, below -300bp gate
    out = score_triggers([r])[0]
    assert "T2_gm_compression" not in out.triggers_fired


# ── T3 FCF burn + short runway ───────────────────────────────────────────────
def test_t3_fires_when_burning_with_under_18mo():
    r = _row(fcf_ttm=-60.0, cash_runway_months=10.0)
    out = score_triggers([r])[0]
    assert "T3_fcf_burn_short_runway" in out.triggers_fired


def test_t3_does_not_fire_with_long_runway():
    r = _row(fcf_ttm=-60.0, cash_runway_months=24.0)
    out = score_triggers([r])[0]
    assert "T3_fcf_burn_short_runway" not in out.triggers_fired


def test_t3_does_not_fire_when_fcf_positive():
    r = _row(fcf_ttm=10.0, cash_runway_months=5.0)
    out = score_triggers([r])[0]
    assert "T3_fcf_burn_short_runway" not in out.triggers_fired


# ── T4 Beneish (external lookup) ─────────────────────────────────────────────
def test_t4_fires_when_beneish_flagged():
    r = _row()
    lookup = {"TEST": {"m_score": -1.5, "gate_result": "FLAGGED"}}
    out = score_triggers([r], beneish_lookup=lookup)[0]
    assert "T4_beneish_flagged" in out.triggers_fired


def test_t4_does_not_fire_when_beneish_clean():
    r = _row()
    lookup = {"TEST": {"m_score": -2.8, "gate_result": "CLEAN"}}
    out = score_triggers([r], beneish_lookup=lookup)[0]
    assert "T4_beneish_flagged" not in out.triggers_fired


def test_t4_evaluates_none_when_no_lookup():
    r = _row()
    out = score_triggers([r])[0]
    assert "T4_beneish_flagged" not in out.triggers_evaluated


# ── T5 debt/EBITDA ───────────────────────────────────────────────────────────
def test_t5_fires_above_4x():
    r = _row(debt_to_ebitda_ttm=5.5)
    out = score_triggers([r])[0]
    assert "T5_debt_to_ebitda" in out.triggers_fired


def test_t5_does_not_fire_at_3x():
    r = _row(debt_to_ebitda_ttm=3.0)
    out = score_triggers([r])[0]
    assert "T5_debt_to_ebitda" not in out.triggers_fired


# ── T6 EV/Sales premium (sector median) ──────────────────────────────────────
def test_t6_fires_when_above_sector_median_multiplier():
    # Build a sector universe with a clean median EV/Sales of 2.0
    cohort = [
        _row(ticker="A", sector="Tech", ev_to_sales_ttm=1.0),
        _row(ticker="B", sector="Tech", ev_to_sales_ttm=2.0),
        _row(ticker="C", sector="Tech", ev_to_sales_ttm=3.0),
        _row(ticker="HIGH", sector="Tech", ev_to_sales_ttm=4.0),  # > 1.5×2.0 = 3.0
    ]
    results = {r.ticker: r for r in score_triggers(cohort)}
    assert "T6_ev_sales_premium" in results["HIGH"].triggers_fired
    assert "T6_ev_sales_premium" not in results["B"].triggers_fired


def test_t6_skipped_when_sector_has_less_than_3_members():
    cohort = [
        _row(ticker="X", sector="Niche", ev_to_sales_ttm=10.0),
        _row(ticker="Y", sector="Niche", ev_to_sales_ttm=11.0),
    ]
    out = {r.ticker: r for r in score_triggers(cohort)}
    # No sector median computed → T6 evaluates None (not evaluated)
    assert "T6_ev_sales_premium" not in out["X"].triggers_evaluated


# ── T7 dilution ──────────────────────────────────────────────────────────────
def test_t7_fires_on_10pct_dilution():
    r = _row(shares_diluted_q=[110.0, 105.0, 102.0, 100.0])  # +10% YoY
    out = score_triggers([r])[0]
    assert "T7_dilution" in out.triggers_fired


def test_t7_does_not_fire_on_5pct_dilution():
    r = _row(shares_diluted_q=[105.0, 103.0, 102.0, 100.0])  # +5% YoY
    out = score_triggers([r])[0]
    assert "T7_dilution" not in out.triggers_fired


# ── Qualification gates ──────────────────────────────────────────────────────
def test_select_qualifying_requires_min_triggers():
    # Single ticker firing only 2 triggers → rejected even with plenty of data
    r = _row(
        ticker="ONLY_TWO",
        revenue_q=[80.0, 90.0, 100.0, 110.0],     # T1 fires
        gross_margin_q=[0.30, 0.35, 0.39, 0.40],  # T2 fires (1000bp compression)
        debt_to_ebitda_ttm=2.0,                   # T5 no
        shares_diluted_q=[101.0, 100.0, 100.0, 100.0],  # T7 no
        fcf_ttm=50.0,                             # T3 no
    )
    results = score_triggers([r])
    selected = select_qualifying(results)
    assert selected == []


def test_select_qualifying_rejects_sparse_evaluable():
    # Fires 3 triggers but only 3 were evaluable — rejected by MIN_EVALUABLE=4
    r = _row(
        ticker="SPARSE",
        revenue_q=[80.0, 90.0, 100.0, 110.0],     # T1 fires (evaluable)
        gross_margin_q=[0.30, 0.35, 0.39, 0.40],  # T2 fires (evaluable)
        fcf_ttm=-60.0, cash_runway_months=8.0,    # T3 fires (evaluable)
        debt_to_ebitda_ttm=None,                  # T5 N/A
        shares_diluted_q=[None, None, None, None],# T7 N/A
        ev_to_sales_ttm=None,                     # T6 N/A
    )
    results = score_triggers([r])
    selected = select_qualifying(results)
    # Only 3 triggers evaluable → fails MIN_EVALUABLE=4 gate
    assert selected == []


def test_select_qualifying_caps_per_sector():
    bear_template = dict(
        revenue_q=[80.0, 90.0, 100.0, 110.0],
        gross_margin_q=[0.30, 0.35, 0.39, 0.40],
        fcf_ttm=-60.0, cash_runway_months=8.0,
        debt_to_ebitda_ttm=5.0,
        shares_diluted_q=[110.0, 105.0, 102.0, 100.0],
        ev_to_sales_ttm=2.0,
    )
    universe = [
        {"ticker": "T1", "sector": "Tech", "market_cap_m": 5000, "adv_k": 10000,
         "short_interest_pct": 5.0, **bear_template},
        {"ticker": "T2", "sector": "Tech", "market_cap_m": 4000, "adv_k": 10000,
         "short_interest_pct": 5.0, **bear_template},
        {"ticker": "T3", "sector": "Tech", "market_cap_m": 3000, "adv_k": 10000,
         "short_interest_pct": 5.0, **bear_template},
    ]
    results = score_triggers(universe)
    selected = select_qualifying(results, max_per_sector=2)
    assert len(selected) == 2
    assert {r.ticker for r in selected} == {"T1", "T2"}  # largest cap wins ties


def test_constants_match_thresholds():
    """Guard against accidental loosening — fail loudly if defaults shift."""
    assert MIN_TRIGGERS == 3
    assert MIN_EVALUABLE == 4
    assert GM_COMPRESSION_BPS == -0.03
    assert FCF_BURN_RUNWAY_MONTHS == 18.0
    assert BENEISH_FLAG_THRESHOLD == -2.22
    assert DEBT_EBITDA_THRESHOLD == 4.0
    assert EV_SALES_SECTOR_MULTIPLIER == 1.5
    assert DILUTION_YOY_THRESHOLD == 0.08


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
