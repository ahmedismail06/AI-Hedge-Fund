"""Tests for the market-expectations inflation layer.

Coverage
--------
- Score boundedness in [-1, +1].
- Score sign: hot breakevens → positive; cool → negative.
- Confidence drops when inputs are missing.
- contributors populated correctly.
- Rolling_zscore used when history provided; tanh_norm fallback otherwise.
- Layer notes describe which path was taken (zscore vs fallback).
"""

from __future__ import annotations

import math

import pytest

from backend.macro.inflation_engine.layers import market_expectations as market_layer
from backend.macro.inflation_engine.types import InflationSnapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snap(
    breakeven_5y=None,
    five_y_five_y_forward=None,
    wti_oil_price=None,
    gasoline_price=None,
    copper_price=None,
    commodity_composite=None,
    history=None,
) -> InflationSnapshot:
    return InflationSnapshot(
        breakeven_5y=breakeven_5y,
        five_y_five_y_forward=five_y_five_y_forward,
        wti_oil_price=wti_oil_price,
        gasoline_price=gasoline_price,
        copper_price=copper_price,
        commodity_composite=commodity_composite,
        history=history or {},
    )


def _full_hot() -> InflationSnapshot:
    """All indicators signalling inflationary pressure."""
    return _snap(
        breakeven_5y=3.0,         # well above 2.25 anchor
        five_y_five_y_forward=3.5,
        wti_oil_price=100.0,      # above fallback center of 70
        gasoline_price=80.0,      # above fallback center of 55
        copper_price=40.0,        # above fallback center of 24
        commodity_composite=30.0, # above fallback center of 22
    )


def _full_cool() -> InflationSnapshot:
    """All indicators signalling disinflationary pressure."""
    return _snap(
        breakeven_5y=1.5,
        five_y_five_y_forward=1.8,
        wti_oil_price=40.0,
        gasoline_price=30.0,
        copper_price=10.0,
        commodity_composite=12.0,
    )


def _at_anchor() -> InflationSnapshot:
    """Indicators at their tanh anchors → score near 0."""
    return _snap(
        breakeven_5y=2.25,   # BREAKEVEN_5Y center
        five_y_five_y_forward=2.30,  # FIVE_Y_FIVE_Y_FWD center
        wti_oil_price=70.0,  # USO fallback center
        gasoline_price=55.0,
        copper_price=24.0,
        commodity_composite=22.0,
    )


# ---------------------------------------------------------------------------
# Boundedness
# ---------------------------------------------------------------------------


def test_market_expectations_bounded_hot():
    result = market_layer.compute(_full_hot())
    assert -1.0 <= result.score <= 1.0
    assert math.isfinite(result.score)


def test_market_expectations_bounded_cool():
    result = market_layer.compute(_full_cool())
    assert -1.0 <= result.score <= 1.0
    assert math.isfinite(result.score)


def test_market_expectations_bounded_extreme():
    snap = _snap(breakeven_5y=10.0, wti_oil_price=500.0)
    result = market_layer.compute(snap)
    assert -1.0 <= result.score <= 1.0


# ---------------------------------------------------------------------------
# Score sign
# ---------------------------------------------------------------------------


def test_market_expectations_hot_positive():
    result = market_layer.compute(_full_hot())
    assert result.score > 0.0, (
        f"Expected positive score for hot inputs, got {result.score}"
    )


def test_market_expectations_cool_negative():
    result = market_layer.compute(_full_cool())
    assert result.score < 0.0, (
        f"Expected negative score for cool inputs, got {result.score}"
    )


def test_market_expectations_at_anchor_near_zero():
    result = market_layer.compute(_at_anchor())
    assert abs(result.score) < 0.15, (
        f"Expected score near 0 at economic anchors, got {result.score}"
    )


def test_market_expectations_breakeven_monotone():
    """Higher breakeven → higher market-expectations score."""
    scores = []
    for be in [1.5, 2.0, 2.25, 2.75, 3.5]:
        snap = _snap(breakeven_5y=be)
        scores.append(market_layer.compute(snap).score)
    for i in range(len(scores) - 1):
        assert scores[i] < scores[i + 1], (
            f"Not monotone at breakeven={[1.5, 2.0, 2.25, 2.75, 3.5][i]}: "
            f"score={scores[i]:.4f} >= {scores[i+1]:.4f}"
        )


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------


def test_market_expectations_confidence_positive_with_data():
    result = market_layer.compute(_full_hot())
    assert result.confidence > 0.0


def test_market_expectations_no_data_zero_confidence():
    result = market_layer.compute(InflationSnapshot())
    assert result.score == 0.0
    assert result.confidence == 0.0


def test_market_expectations_partial_data_lower_confidence():
    """Fewer indicators → lower confidence."""
    full = market_layer.compute(_full_hot())
    partial = market_layer.compute(_snap(breakeven_5y=3.0))
    assert partial.confidence < full.confidence, (
        f"Partial ({partial.confidence:.3f}) should be < full ({full.confidence:.3f})"
    )


# ---------------------------------------------------------------------------
# Rolling_zscore path
# ---------------------------------------------------------------------------


def test_market_expectations_zscore_when_history_available():
    """When history provided, commodity scoring uses rolling_zscore path."""
    history_wti = list(range(60, 90))  # 30 data points
    snap = _snap(
        wti_oil_price=85.0,  # high vs history
        history={"wti_oil_price": history_wti},
    )
    result = market_layer.compute(snap)
    # Score should be positive (price above history mean)
    assert result.score > 0.0
    # Notes should mention rolling_zscore
    zscore_notes = [n for n in result.notes if "rolling_zscore" in n.lower()]
    assert len(zscore_notes) >= 1, f"Expected rolling_zscore note; got notes: {result.notes}"


def test_market_expectations_tanh_fallback_when_no_history():
    """When no history provided, commodity scoring uses tanh_norm fallback."""
    snap = _snap(wti_oil_price=100.0)
    result = market_layer.compute(snap)
    # Score > 0 (above fallback center of 70)
    assert result.score > 0.0
    # Notes should mention tanh_norm fallback
    fallback_notes = [n for n in result.notes if "tanh_norm fallback" in n]
    assert len(fallback_notes) >= 1, f"Expected tanh_norm fallback note; got: {result.notes}"


# ---------------------------------------------------------------------------
# Contributors
# ---------------------------------------------------------------------------


def test_market_expectations_contributors_all_present():
    result = market_layer.compute(_full_hot())
    assert len(result.contributors) == 6


def test_market_expectations_contributors_fields():
    result = market_layer.compute(_snap(breakeven_5y=2.5))
    for contrib in result.contributors:
        assert -1.0 <= contrib.signal <= 1.0
        assert 0.0 < contrib.weight <= 1.0
        assert math.isfinite(contrib.raw_value)
