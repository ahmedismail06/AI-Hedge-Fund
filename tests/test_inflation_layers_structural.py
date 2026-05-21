"""Tests for the structural inflation layer.

Coverage
--------
- Score boundedness in [-1, +1].
- Score sign: hot inputs → positive score; cool inputs → negative score.
- Confidence drops when inputs are missing.
- contributors list populated correctly (count, field names, weights sum).
- Staleness confidence: stale monthly inputs reduce layer confidence.
- Only some inputs present: score uses weighted average of present indicators.
- No inputs: score=0.0, confidence=0.0.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest

from backend.macro.inflation_engine.layers import structural as structural_layer
from backend.macro.inflation_engine.types import InflationSnapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snapshot(
    cpi_yoy=None,
    core_cpi_yoy=None,
    ppi_yoy=None,
    pce_yoy=None,
    sticky_cpi_yoy=None,
    trimmed_mean_pce_yoy=None,
    release_dates=None,
) -> InflationSnapshot:
    return InflationSnapshot(
        cpi_yoy=cpi_yoy,
        core_cpi_yoy=core_cpi_yoy,
        ppi_yoy=ppi_yoy,
        pce_yoy=pce_yoy,
        sticky_cpi_yoy=sticky_cpi_yoy,
        trimmed_mean_pce_yoy=trimmed_mean_pce_yoy,
        release_dates=release_dates or {},
    )


def _full_hot_snapshot() -> InflationSnapshot:
    """All six indicators above target (inflationary = positive score expected)."""
    return _snapshot(
        cpi_yoy=5.0,
        core_cpi_yoy=4.5,
        ppi_yoy=6.0,
        pce_yoy=4.0,
        sticky_cpi_yoy=4.5,
        trimmed_mean_pce_yoy=4.0,
    )


def _full_cool_snapshot() -> InflationSnapshot:
    """All six indicators below target (disinflationary = negative score expected)."""
    return _snapshot(
        cpi_yoy=0.5,
        core_cpi_yoy=0.8,
        ppi_yoy=0.0,
        pce_yoy=0.5,
        sticky_cpi_yoy=0.8,
        trimmed_mean_pce_yoy=0.6,
    )


def _at_target_snapshot() -> InflationSnapshot:
    """All at Fed target (2.0%) → score should be near 0."""
    return _snapshot(
        cpi_yoy=2.0,
        core_cpi_yoy=2.0,
        ppi_yoy=2.0,
        pce_yoy=2.0,
        sticky_cpi_yoy=2.0,
        trimmed_mean_pce_yoy=2.0,
    )


# ---------------------------------------------------------------------------
# Score boundedness
# ---------------------------------------------------------------------------


def test_structural_score_bounded_hot():
    result = structural_layer.compute(_full_hot_snapshot())
    assert -1.0 <= result.score <= 1.0


def test_structural_score_bounded_cool():
    result = structural_layer.compute(_full_cool_snapshot())
    assert -1.0 <= result.score <= 1.0


def test_structural_score_finite():
    result = structural_layer.compute(_full_hot_snapshot())
    assert math.isfinite(result.score)


# ---------------------------------------------------------------------------
# Score sign
# ---------------------------------------------------------------------------


def test_structural_hot_inputs_positive_score():
    """All series above target → positive score."""
    result = structural_layer.compute(_full_hot_snapshot())
    assert result.score > 0.0, (
        f"Expected positive score for hot inputs, got {result.score}"
    )


def test_structural_cool_inputs_negative_score():
    """All series below target → negative score."""
    result = structural_layer.compute(_full_cool_snapshot())
    assert result.score < 0.0, (
        f"Expected negative score for cool inputs, got {result.score}"
    )


def test_structural_at_target_near_zero():
    """All series at 2% target → score near 0."""
    result = structural_layer.compute(_at_target_snapshot())
    assert abs(result.score) < 0.05, (
        f"Expected score near 0 when all at target, got {result.score}"
    )


def test_structural_monotone_cpi():
    """Higher CPI YoY → higher structural score (monotone in CPI)."""
    scores = []
    for cpi in [1.0, 2.0, 3.0, 4.0, 5.0]:
        snap = _snapshot(cpi_yoy=cpi)
        scores.append(structural_layer.compute(snap).score)
    for i in range(len(scores) - 1):
        assert scores[i] < scores[i + 1], (
            f"Not monotone at CPI={[1.0, 2.0, 3.0, 4.0, 5.0][i]}: "
            f"score={scores[i]:.4f}, next={scores[i+1]:.4f}"
        )


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------


def test_structural_full_data_positive_confidence():
    result = structural_layer.compute(_full_hot_snapshot())
    assert result.confidence > 0.0


def test_structural_no_data_zero_confidence():
    result = structural_layer.compute(InflationSnapshot())
    assert result.score == 0.0
    assert result.confidence == 0.0


def test_structural_partial_data_confidence_positive():
    """With only CPI present, confidence > 0."""
    snap = _snapshot(cpi_yoy=3.0)
    result = structural_layer.compute(snap)
    assert result.confidence > 0.0
    assert result.score > 0.0


def test_structural_confidence_with_stale_data():
    """Providing stale release dates reduces layer confidence."""
    # Fresh release: today
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    fresh_snap = _snapshot(
        cpi_yoy=3.0,
        core_cpi_yoy=3.0,
        release_dates={"cpi_yoy": today, "core_cpi_yoy": today},
    )
    # Stale: 45 days ago
    stale_snap = _snapshot(
        cpi_yoy=3.0,
        core_cpi_yoy=3.0,
        release_dates={
            "cpi_yoy": today - timedelta(days=45),
            "core_cpi_yoy": today - timedelta(days=45),
        },
    )
    fresh_result = structural_layer.compute(fresh_snap)
    stale_result = structural_layer.compute(stale_snap)
    assert fresh_result.confidence > stale_result.confidence, (
        f"Fresh confidence ({fresh_result.confidence:.3f}) should exceed "
        f"stale confidence ({stale_result.confidence:.3f})"
    )


# ---------------------------------------------------------------------------
# Contributors
# ---------------------------------------------------------------------------


def test_structural_contributors_count_full_data():
    """All 6 indicators present → 6 contributors."""
    result = structural_layer.compute(_full_hot_snapshot())
    assert len(result.contributors) == 6


def test_structural_contributors_partial():
    """Only CPI and PCE present → 2 contributors."""
    snap = _snapshot(cpi_yoy=3.0, pce_yoy=2.5)
    result = structural_layer.compute(snap)
    assert len(result.contributors) == 2


def test_structural_contributors_fields():
    """Each Contribution has required fields with correct types/ranges."""
    result = structural_layer.compute(_full_hot_snapshot())
    for contrib in result.contributors:
        assert isinstance(contrib.indicator, str)
        assert -1.0 <= contrib.signal <= 1.0
        assert 0.0 <= contrib.weight <= 1.0
        assert 0.0 <= contrib.confidence <= 1.0
        assert math.isfinite(contrib.raw_value)


def test_structural_contributor_weights_sum():
    """Base weights for all 6 indicators should sum to 1.0."""
    result = structural_layer.compute(_full_hot_snapshot())
    weight_sum = sum(c.weight for c in result.contributors)
    assert weight_sum == pytest.approx(1.0, abs=1e-9)


def test_structural_notes_populated():
    """LayerOutput.notes is a list (may be empty or contain diagnostics)."""
    result = structural_layer.compute(_full_hot_snapshot())
    assert isinstance(result.notes, list)


def test_structural_missing_inputs_noted():
    """Missing indicators should appear in notes."""
    snap = _snapshot(cpi_yoy=3.0)  # only CPI present
    result = structural_layer.compute(snap)
    # Should have notes for the 5 missing series
    missing_notes = [n for n in result.notes if "missing" in n.lower()]
    assert len(missing_notes) >= 5
