"""
Tests for the Beneish EXCLUDED auto-enqueue quality gates.

The screener auto-routes Beneish-flagged tickers into short_candidates. Without
filters, REITs, half-missing M-scores, illiquid names, and fundamentally healthy
businesses all leak through. This test set locks in the four false-positive
filters using real shapes pulled from a live run.
"""
from __future__ import annotations

import pytest

from backend.agents.screening_agent import _beneish_enqueue_skip_reason
from backend.screener.scorer import ScreenerResult


def _excluded(**kwargs) -> ScreenerResult:
    """Build an EXCLUDED ScreenerResult. Defaults pass all four gates."""
    defaults = dict(
        ticker="TEST",
        composite_score=0.0,
        quality_score=0.0,
        value_score=0.0,
        momentum_score=0.0,
        sector="Consumer",
        market_cap_m=500.0,
        adv_k=20_000.0,
        beneish_m_score=1.5,
        beneish_flag="EXCLUDED",
        excluded=True,
        raw_factors={
            "beneish": {"missing_fields": []},
            "quality": {"roic": -0.10, "revenue_growth_yoy": 0.5, "fcf_conversion": -0.5},
        },
    )
    defaults.update(kwargs)
    return ScreenerResult(**defaults)


# ── Real-world shapes from the user's live run ───────────────────────────────
def test_aomr_shape_rejected():
    """AOMR — REIT, $700K ADV, 4 missing fields, healthy fundamentals. All four gates fire."""
    r = _excluded(
        ticker="AOMR",
        sector="Real Estate",
        market_cap_m=207.04,
        adv_k=699.90,
        beneish_m_score=-1.6655,
        raw_factors={
            "beneish": {"missing_fields": ["DSRI", "GMI", "AQI", "DEPI"]},
            "quality": {
                "roic": 0.2481,
                "revenue_growth_yoy": 0.3009,
                "fcf_conversion": -1.0,  # not >= 0 — but G1 fires first anyway
            },
        },
    )
    reason = _beneish_enqueue_skip_reason(r)
    assert reason is not None
    # First-firing gate is G1 (sector) because we evaluate in order
    assert "Real Estate" in reason


def test_aspi_shape_passes():
    """ASPI — Consumer, $22M ADV, 2 missing fields, ROIC −38%. Passes all gates."""
    r = _excluded(
        ticker="ASPI",
        sector="Consumer",
        market_cap_m=730.24,
        adv_k=22_013.10,
        beneish_m_score=1.9295,
        raw_factors={
            "beneish": {"missing_fields": ["DSRI", "DEPI"]},
            "quality": {
                "roic": -0.3826,
                "revenue_growth_yoy": 4.7548,
                "fcf_conversion": None,
            },
        },
    )
    assert _beneish_enqueue_skip_reason(r) is None


# ── Gate-by-gate isolation ───────────────────────────────────────────────────
def test_g1_real_estate_rejected():
    r = _excluded(sector="Real Estate")
    assert "Real Estate" in (_beneish_enqueue_skip_reason(r) or "")


def test_g1_financials_rejected():
    r = _excluded(sector="Financials")
    assert "Financials" in (_beneish_enqueue_skip_reason(r) or "")


def test_g1_utilities_rejected():
    r = _excluded(sector="Utilities")
    assert "Utilities" in (_beneish_enqueue_skip_reason(r) or "")


def test_g2_three_missing_fields_rejected():
    r = _excluded(raw_factors={
        "beneish": {"missing_fields": ["DSRI", "GMI", "AQI"]},
        "quality": {"roic": -0.1, "revenue_growth_yoy": 0.5, "fcf_conversion": -0.5},
    })
    assert "low-confidence" in (_beneish_enqueue_skip_reason(r) or "")


def test_g2_two_missing_fields_passes():
    r = _excluded(raw_factors={
        "beneish": {"missing_fields": ["DSRI", "DEPI"]},
        "quality": {"roic": -0.1, "revenue_growth_yoy": 0.5, "fcf_conversion": -0.5},
    })
    assert _beneish_enqueue_skip_reason(r) is None


def test_g3_low_adv_rejected():
    r = _excluded(adv_k=3_000.0)  # under $5M floor
    assert "illiquid" in (_beneish_enqueue_skip_reason(r) or "")


def test_g3_adv_at_floor_passes():
    r = _excluded(adv_k=5_000.0)  # exactly $5M
    assert _beneish_enqueue_skip_reason(r) is None


def test_g4_healthy_fundamentals_rejected():
    """ROIC > 15%, revenue growing, FCF conversion non-negative — Beneish probably wrong."""
    r = _excluded(raw_factors={
        "beneish": {"missing_fields": []},
        "quality": {"roic": 0.25, "revenue_growth_yoy": 0.20, "fcf_conversion": 0.80},
    })
    assert "fundamentals healthy" in (_beneish_enqueue_skip_reason(r) or "")


def test_g4_negative_roic_passes():
    """Money-loser with same growth and FCF — G4 should NOT fire."""
    r = _excluded(raw_factors={
        "beneish": {"missing_fields": []},
        "quality": {"roic": -0.05, "revenue_growth_yoy": 0.20, "fcf_conversion": 0.80},
    })
    assert _beneish_enqueue_skip_reason(r) is None


def test_g4_growing_but_burning_cash_passes():
    """High ROIC and growth but negative FCF conversion — G4 should NOT fire."""
    r = _excluded(raw_factors={
        "beneish": {"missing_fields": []},
        "quality": {"roic": 0.25, "revenue_growth_yoy": 0.20, "fcf_conversion": -0.30},
    })
    assert _beneish_enqueue_skip_reason(r) is None


# ── Missing-data safety ──────────────────────────────────────────────────────
def test_no_raw_factors_does_not_crash():
    r = _excluded(raw_factors={})
    # No data → no gate can fire → no skip. Conservative: let it through.
    assert _beneish_enqueue_skip_reason(r) is None


def test_missing_adv_does_not_crash():
    r = _excluded(adv_k=None)
    # Can't evaluate G3 — let it through
    assert _beneish_enqueue_skip_reason(r) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
