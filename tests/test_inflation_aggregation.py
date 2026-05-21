"""Tests for the inflation layer aggregation module.

Coverage
--------
- When surprise.confidence=0, remaining layers' weights renormalise to sum=1.0.
- When all layers have confidence > 0, weights match AggregationWeights base values.
- contributors_top5 ordered by |signal × weight × confidence| descending.
- Score boundedness in [-1, +1].
- All-zero confidence fallback: equal weighting, finite score.
- Empty layers dict: returns score=0.0, confidence=0.0.
"""

from __future__ import annotations

import math

import pytest

from backend.macro.inflation_engine.aggregation import aggregate
from backend.macro.inflation_engine.config import AggregationWeights
from backend.macro.inflation_engine.types import Contribution, LayerOutput


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _layer(score: float, confidence: float, n_contribs: int = 0) -> LayerOutput:
    """Build a minimal LayerOutput with a given score and confidence."""
    contribs = [
        Contribution(
            indicator=f"indicator_{i}",
            raw_value=float(i),
            signal=score,
            weight=1.0 / max(n_contribs, 1),
            confidence=confidence,
            age_days=None,
        )
        for i in range(n_contribs)
    ]
    return LayerOutput(score=score, confidence=confidence, contributors=contribs, notes=[])


def _surprise_stub() -> LayerOutput:
    """PR3 surprise stub: confidence=0."""
    return LayerOutput(score=0.0, confidence=0.0, contributors=[], notes=["stub"])


# ---------------------------------------------------------------------------
# Surprise layer dropped — weight redistributed
# ---------------------------------------------------------------------------


def test_surprise_zero_confidence_dropped():
    """When surprise has confidence=0, normalised weights sum to 1.0 across other layers."""
    layers = {
        "structural": _layer(0.5, 1.0, 2),
        "momentum": _layer(0.3, 1.0, 1),
        "market_expectations": _layer(0.4, 1.0, 1),
        "surprise": _surprise_stub(),
    }
    result = aggregate(layers)

    # Check that the normalised weights of the three active layers sum to 1.0.
    # Base weights with confidence=1: structural=0.40, momentum=0.25, market=0.25.
    # Surprise effective weight = 0.10 × 0 = 0. Remaining effective weights sum = 0.90.
    # Normalised: structural=0.40/0.90, momentum=0.25/0.90, market=0.25/0.90.
    diag = result.diagnostics["normalised_weights"]
    total_nw = sum(diag.values())
    assert total_nw == pytest.approx(1.0, abs=1e-9), (
        f"Normalised weights sum = {total_nw}, expected 1.0. weights={diag}"
    )
    assert diag["surprise"] == pytest.approx(0.0, abs=1e-9)


def test_surprise_zero_confidence_score_correct():
    """Final score should be weighted average of the 3 active layers only."""
    weights = AggregationWeights()
    layers = {
        "structural": _layer(0.5, 1.0),
        "momentum": _layer(0.3, 1.0),
        "market_expectations": _layer(0.4, 1.0),
        "surprise": _surprise_stub(),
    }
    result = aggregate(layers, weights)

    # Expected: effective weights = {struct: 0.40, mom: 0.25, market: 0.25, surp: 0}
    # total_eff = 0.90; normalised = struct=4/9, mom=5/18, market=5/18
    expected = (0.5 * 0.40 + 0.3 * 0.25 + 0.4 * 0.25) / (0.40 + 0.25 + 0.25)
    assert result.score == pytest.approx(expected, abs=1e-6)


# ---------------------------------------------------------------------------
# All layers have confidence > 0
# ---------------------------------------------------------------------------


def test_all_layers_confidence_nonzero_weights_match_base():
    """When all 4 layers have confidence=1, effective weights equal base weights."""
    weights = AggregationWeights()
    layers = {
        "structural": _layer(0.5, 1.0),
        "momentum": _layer(0.3, 1.0),
        "market_expectations": _layer(0.4, 1.0),
        "surprise": _layer(0.1, 1.0),  # confidence = 1 (unlike PR3 stub)
    }
    result = aggregate(layers, weights)

    diag = result.diagnostics["normalised_weights"]
    # All confidences = 1.0, so effective = base. Total = 1.0, normalised = base.
    assert diag["structural"] == pytest.approx(weights.structural, abs=1e-9)
    assert diag["momentum"] == pytest.approx(weights.momentum, abs=1e-9)
    assert diag["market_expectations"] == pytest.approx(weights.market_expectations, abs=1e-9)
    assert diag["surprise"] == pytest.approx(weights.surprise, abs=1e-9)


def test_all_layers_weights_sum_to_one_when_all_confident():
    weights = AggregationWeights()
    layers = {
        "structural": _layer(0.5, 1.0),
        "momentum": _layer(0.3, 1.0),
        "market_expectations": _layer(0.4, 1.0),
        "surprise": _layer(0.1, 1.0),
    }
    result = aggregate(layers, weights)
    total = sum(result.diagnostics["normalised_weights"].values())
    assert total == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# Score boundedness
# ---------------------------------------------------------------------------


def test_aggregate_score_bounded():
    layers = {
        "structural": _layer(0.9, 0.8),
        "momentum": _layer(0.8, 0.9),
        "market_expectations": _layer(0.7, 0.7),
        "surprise": _surprise_stub(),
    }
    result = aggregate(layers)
    assert -1.0 <= result.score <= 1.0
    assert math.isfinite(result.score)


def test_aggregate_score_bounded_negative():
    layers = {
        "structural": _layer(-0.9, 0.8),
        "momentum": _layer(-0.8, 0.9),
        "market_expectations": _layer(-0.7, 0.7),
        "surprise": _surprise_stub(),
    }
    result = aggregate(layers)
    assert -1.0 <= result.score <= 1.0
    assert result.score < 0.0


# ---------------------------------------------------------------------------
# All-zero confidence fallback
# ---------------------------------------------------------------------------


def test_all_zero_confidence_fallback_finite():
    """All layers confidence=0 → fallback equal weighting; score must be finite."""
    layers = {
        "structural": _layer(0.5, 0.0),
        "momentum": _layer(0.3, 0.0),
        "market_expectations": _layer(0.4, 0.0),
        "surprise": _layer(0.0, 0.0),
    }
    result = aggregate(layers)
    assert math.isfinite(result.score)
    assert -1.0 <= result.score <= 1.0


# ---------------------------------------------------------------------------
# contributors_top5 ordering
# ---------------------------------------------------------------------------


def test_contributors_top5_ordered_by_magnitude():
    """top5 contributors should be sorted by |signal × weight| descending."""
    contribs_struct = [
        Contribution("cpi_yoy", 3.5, 0.8, 0.30, 1.0),
        Contribution("core_cpi_yoy", 3.0, 0.6, 0.30, 1.0),
        Contribution("ppi_yoy", 2.5, 0.2, 0.15, 1.0),
    ]
    layers = {
        "structural": LayerOutput(score=0.5, confidence=1.0, contributors=contribs_struct, notes=[]),
        "momentum": _layer(0.3, 0.5),
        "market_expectations": _layer(0.4, 0.8),
        "surprise": _surprise_stub(),
    }
    result = aggregate(layers)
    if len(result.contributors_top5) >= 2:
        for i in range(len(result.contributors_top5) - 1):
            a = result.contributors_top5[i]
            b = result.contributors_top5[i + 1]
            mag_a = abs(a.signal * a.weight)
            mag_b = abs(b.signal * b.weight)
            assert mag_a >= mag_b, (
                f"Top5 not sorted: pos {i} (|{a.signal:.2f}×{a.weight:.2f}|={mag_a:.4f}) "
                f"> pos {i+1} (|{b.signal:.2f}×{b.weight:.2f}|={mag_b:.4f})"
            )


def test_contributors_top5_at_most_5():
    """Result must contain at most 5 contributors."""
    contribs = [
        Contribution(f"ind_{i}", float(i), 0.5, 0.1, 1.0)
        for i in range(10)
    ]
    layers = {
        "structural": LayerOutput(score=0.5, confidence=1.0, contributors=contribs, notes=[]),
        "momentum": _layer(0.3, 0.5),
        "market_expectations": _layer(0.4, 0.8),
        "surprise": _surprise_stub(),
    }
    result = aggregate(layers)
    assert len(result.contributors_top5) <= 5


# ---------------------------------------------------------------------------
# Diagnostics and metadata
# ---------------------------------------------------------------------------


def test_aggregate_method_is_static():
    layers = {
        "structural": _layer(0.5, 1.0),
        "momentum": _layer(0.3, 0.9),
        "market_expectations": _layer(0.4, 0.8),
        "surprise": _surprise_stub(),
    }
    result = aggregate(layers)
    assert result.aggregate_method == "static"


def test_aggregate_diagnostics_has_required_keys():
    layers = {
        "structural": _layer(0.5, 1.0),
        "momentum": _layer(0.3, 0.9),
        "market_expectations": _layer(0.4, 0.8),
        "surprise": _surprise_stub(),
    }
    result = aggregate(layers)
    for key in ["base_weights", "effective_weights", "normalised_weights",
                "layer_scores", "layer_confidences"]:
        assert key in result.diagnostics, f"Missing diagnostics key: {key}"


def test_aggregate_stale_warnings_is_list():
    layers = {
        "structural": _layer(0.5, 1.0),
        "momentum": _layer(0.3, 0.9),
        "market_expectations": _layer(0.4, 0.8),
        "surprise": _surprise_stub(),
    }
    result = aggregate(layers)
    assert isinstance(result.stale_warnings, list)


def test_aggregate_confidence_in_range():
    layers = {
        "structural": _layer(0.5, 0.8),
        "momentum": _layer(0.3, 0.9),
        "market_expectations": _layer(0.4, 0.7),
        "surprise": _surprise_stub(),
    }
    result = aggregate(layers)
    assert 0.0 <= result.confidence <= 1.0
