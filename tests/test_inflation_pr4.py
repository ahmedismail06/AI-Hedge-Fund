"""Tests for PR 4 features: Surprise layer and Dynamic weighting.
"""

import pytest
from datetime import datetime, date
from backend.macro.inflation_engine.types import (
    InflationSnapshot,
    ConsensusExpectation,
    LayerOutput,
)
from backend.macro.inflation_engine.layers import surprise as surprise_layer
from backend.macro.inflation_engine.aggregation import aggregate


def test_surprise_layer_scoring():
    """Test that the surprise layer correctly scores actual-vs-consensus."""
    # Case 1: Positive surprise (Inflation higher than expected)
    exp_pos = ConsensusExpectation(
        indicator="cpi_yoy",
        expected_value=3.0,
        actual_value=3.2,
        release_dt=datetime(2026, 5, 21),
        source="Manual"
    )
    snapshot = InflationSnapshot(expectations=[exp_pos])
    output = surprise_layer.compute(snapshot)
    
    assert output.score > 0
    assert output.confidence == 1.0
    assert len(output.contributors) == 1
    assert output.contributors[0].indicator == "surprise_cpi_yoy"
    assert output.contributors[0].raw_value == pytest.approx(0.2)
    # tanh(0.2 / 0.2) = tanh(1) approx 0.76
    assert output.score == pytest.approx(0.76159, abs=0.01)

    # Case 2: Negative surprise
    exp_neg = ConsensusExpectation(
        indicator="cpi_yoy",
        expected_value=3.0,
        actual_value=2.8,
        release_dt=datetime(2026, 5, 21),
        source="Manual"
    )
    snapshot = InflationSnapshot(expectations=[exp_neg])
    output = surprise_layer.compute(snapshot)
    assert output.score < 0
    assert output.score == pytest.approx(-0.76159, abs=0.01)

    # Case 3: No surprise (on target)
    exp_zero = ConsensusExpectation(
        indicator="cpi_yoy",
        expected_value=3.0,
        actual_value=3.0,
        release_dt=datetime(2026, 5, 21),
        source="Manual"
    )
    snapshot = InflationSnapshot(expectations=[exp_zero])
    output = surprise_layer.compute(snapshot)
    assert output.score == 0.0


def test_surprise_layer_multi_indicator():
    """Test that the surprise layer averages multiple surprises."""
    exp1 = ConsensusExpectation(
        indicator="cpi_yoy", expected_value=3.0, actual_value=3.2,
        release_dt=datetime(2026, 5, 21), source="Manual"
    )
    exp2 = ConsensusExpectation(
        indicator="pce_yoy", expected_value=2.0, actual_value=2.0,
        release_dt=datetime(2026, 5, 21), source="Manual"
    )
    snapshot = InflationSnapshot(expectations=[exp1, exp2])
    output = surprise_layer.compute(snapshot)
    
    # average of tanh(1) and tanh(0) = (0.76 + 0) / 2 = 0.38
    assert output.score == pytest.approx(0.38, abs=0.01)
    assert len(output.contributors) == 2


def test_dynamic_weighting_ic_scaling():
    """Test that aggregate() respects IC-based scaling."""
    layers = {
        "structural": LayerOutput(score=1.0, confidence=1.0, contributors=[], notes=[]),
        "momentum": LayerOutput(score=0.0, confidence=1.0, contributors=[], notes=[]),
        "market_expectations": LayerOutput(score=0.0, confidence=0.0, contributors=[], notes=[]),
        "surprise": LayerOutput(score=0.0, confidence=0.0, contributors=[], notes=[]),
    }
    
    # 1. Static case (IC=1.0 for all)
    res_static = aggregate(layers, layer_ics=None)
    # Weights: Structural=0.4, Momentum=0.25, Market=0.25, Surprise=0.1
    # Active: Structural, Momentum. Total base weight = 0.4 + 0.25 = 0.65
    # Normalised Structural = 0.4 / 0.65 approx 0.615
    assert res_static.score == pytest.approx(0.615, abs=0.01)
    assert res_static.aggregate_method == "static"

    # 2. Dynamic case: Momentum has very low IC (0.1)
    # IC multiplier for momentum = max(0.5, 0.1) = 0.5
    # Effective Structural = 0.4 * 1.0 * 1.0 = 0.4
    # Effective Momentum = 0.25 * 1.0 * 0.5 = 0.125
    # Total effective = 0.525
    # Normalised Structural = 0.4 / 0.525 approx 0.762
    res_dynamic = aggregate(layers, layer_ics={"structural": 1.0, "momentum": 0.1})
    assert res_dynamic.score == pytest.approx(0.762, abs=0.01)
    assert res_dynamic.aggregate_method == "dynamic"
    assert res_dynamic.diagnostics["normalised_weights"]["structural"] == pytest.approx(0.762, abs=0.01)


def test_stability_noise_injection():
    """Stability test: simulate ±10% noise on each input, score stays bounded."""
    # Base snapshot
    base_snapshot = InflationSnapshot(
        cpi_yoy=3.0,
        core_cpi_yoy=2.8,
        ppi_yoy=4.0,
        pce_yoy=2.5,
        breakeven_5y=2.3,
    )
    
    # Base scoring
    from backend.macro.inflation_engine import score_inflation
    res_base = score_inflation(base_snapshot)
    
    # Inject 10% noise (additive 0.1 to each indicator)
    noisy_snapshot = InflationSnapshot(
        cpi_yoy=3.3,
        core_cpi_yoy=3.1,
        ppi_yoy=4.4,
        pce_yoy=2.75,
        breakeven_5y=2.53,
    )
    res_noisy = score_inflation(noisy_snapshot)
    
    # Score variance should be bounded. A 10% move in all inputs shouldn't
    # cause the score to jump from 0 to 1 or flip signs wildly.
    # For these inputs, score should move moderately.
    delta = abs(res_noisy.score - res_base.score)
    assert delta < 0.3  # Moderate change for 10% relative move in all inputs
    assert -1.0 <= res_noisy.score <= 1.0
