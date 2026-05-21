"""Tests for the momentum inflation layer.

Coverage
--------
- Score boundedness in [-1, +1].
- Score sign: accelerating CPI → positive; decelerating → negative.
- Confidence = fraction of expected inputs present.
- contributors populated with correct fields.
- Only some inputs present: partial confidence.
- No inputs: score=0.0, confidence=0.0.
"""

from __future__ import annotations

import math

import pytest

from backend.macro.inflation_engine.layers import momentum as momentum_layer
from backend.macro.inflation_engine.types import InflationSnapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snap(
    cpi_yoy_3m=None,
    core_cpi_yoy_3m=None,
    cpi_slope_6m=None,
) -> InflationSnapshot:
    return InflationSnapshot(
        cpi_yoy_3m=cpi_yoy_3m,
        core_cpi_yoy_3m=core_cpi_yoy_3m,
        cpi_slope_6m=cpi_slope_6m,
    )


# ---------------------------------------------------------------------------
# Boundedness
# ---------------------------------------------------------------------------


def test_momentum_bounded_accelerating():
    snap = _snap(cpi_yoy_3m=3.0, core_cpi_yoy_3m=2.5, cpi_slope_6m=0.3)
    result = momentum_layer.compute(snap)
    assert -1.0 <= result.score <= 1.0
    assert math.isfinite(result.score)


def test_momentum_bounded_decelerating():
    snap = _snap(cpi_yoy_3m=-2.0, core_cpi_yoy_3m=-1.5, cpi_slope_6m=-0.2)
    result = momentum_layer.compute(snap)
    assert -1.0 <= result.score <= 1.0
    assert math.isfinite(result.score)


def test_momentum_bounded_extreme():
    snap = _snap(cpi_yoy_3m=20.0, core_cpi_yoy_3m=20.0, cpi_slope_6m=5.0)
    result = momentum_layer.compute(snap)
    assert -1.0 <= result.score <= 1.0


# ---------------------------------------------------------------------------
# Score sign
# ---------------------------------------------------------------------------


def test_momentum_accelerating_positive():
    """All momentum inputs positive (accelerating) → positive score."""
    snap = _snap(cpi_yoy_3m=2.0, core_cpi_yoy_3m=1.5, cpi_slope_6m=0.2)
    result = momentum_layer.compute(snap)
    assert result.score > 0.0, f"Expected positive for accelerating inputs, got {result.score}"


def test_momentum_decelerating_negative():
    """All momentum inputs negative (decelerating) → negative score."""
    snap = _snap(cpi_yoy_3m=-2.0, core_cpi_yoy_3m=-1.5, cpi_slope_6m=-0.2)
    result = momentum_layer.compute(snap)
    assert result.score < 0.0, f"Expected negative for decelerating inputs, got {result.score}"


def test_momentum_flat_near_zero():
    """All momentum inputs = 0 (flat trend) → score near 0."""
    snap = _snap(cpi_yoy_3m=0.0, core_cpi_yoy_3m=0.0, cpi_slope_6m=0.0)
    result = momentum_layer.compute(snap)
    assert abs(result.score) < 0.01, f"Expected near-zero for flat trend, got {result.score}"


def test_momentum_score_increases_with_acceleration():
    """Larger positive 3m CPI → higher score (monotone)."""
    scores = []
    for val in [-2.0, -1.0, 0.0, 1.0, 2.0]:
        snap = _snap(cpi_yoy_3m=val)
        scores.append(momentum_layer.compute(snap).score)
    for i in range(len(scores) - 1):
        assert scores[i] <= scores[i + 1], (
            f"Not monotone at index {i}: {scores[i]:.4f} > {scores[i+1]:.4f}"
        )


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------


def test_momentum_all_present_full_confidence():
    """All 3 inputs present → confidence = 1.0."""
    snap = _snap(cpi_yoy_3m=1.0, core_cpi_yoy_3m=0.5, cpi_slope_6m=0.1)
    result = momentum_layer.compute(snap)
    assert result.confidence == pytest.approx(1.0)


def test_momentum_two_present_two_thirds_confidence():
    """2 of 3 inputs present → confidence ≈ 2/3."""
    snap = _snap(cpi_yoy_3m=1.0, core_cpi_yoy_3m=0.5)
    result = momentum_layer.compute(snap)
    assert result.confidence == pytest.approx(2.0 / 3.0, abs=1e-9)


def test_momentum_one_present_one_third_confidence():
    """1 of 3 inputs present → confidence ≈ 1/3."""
    snap = _snap(cpi_yoy_3m=1.0)
    result = momentum_layer.compute(snap)
    assert result.confidence == pytest.approx(1.0 / 3.0, abs=1e-9)


def test_momentum_no_inputs_zero_confidence():
    result = momentum_layer.compute(InflationSnapshot())
    assert result.score == 0.0
    assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# Contributors
# ---------------------------------------------------------------------------


def test_momentum_contributors_all_present():
    snap = _snap(cpi_yoy_3m=1.0, core_cpi_yoy_3m=0.5, cpi_slope_6m=0.1)
    result = momentum_layer.compute(snap)
    assert len(result.contributors) == 3


def test_momentum_contributors_partial():
    snap = _snap(cpi_yoy_3m=1.0, cpi_slope_6m=0.1)
    result = momentum_layer.compute(snap)
    assert len(result.contributors) == 2


def test_momentum_contributors_fields():
    snap = _snap(cpi_yoy_3m=2.0, core_cpi_yoy_3m=1.5, cpi_slope_6m=0.3)
    result = momentum_layer.compute(snap)
    for contrib in result.contributors:
        assert -1.0 <= contrib.signal <= 1.0
        assert 0.0 < contrib.weight <= 1.0
        assert math.isfinite(contrib.raw_value)


def test_momentum_notes_is_list():
    result = momentum_layer.compute(InflationSnapshot())
    assert isinstance(result.notes, list)
    assert len(result.notes) > 0  # should note that inputs are missing
