"""Layer aggregation for the inflation scoring engine.

PR 3 of the inflation engine redesign (docs/inflation_engine_design.md §2.6).

Combines the four layer scores into a final InflationScoreResult.

Weighting formula::

    effective_weight_i = base_weight_i × layer_i.confidence
    final_score = Σ(score_i × effective_weight_i) / Σ(effective_weight_i)

When a layer has confidence=0 (e.g. surprise layer in PR3), it is dropped and
the remaining layers' weights are renormalised to sum to 1.0 — the surprise
layer's 10% is redistributed proportionally to structural/momentum/market.

The output ``InflationScoreResult.aggregate_method`` is ``"static"`` in PR3.
PR4 will add ``"dynamic"`` (IC-based weight shifting).
"""

from __future__ import annotations

import logging
from typing import Optional

from backend.macro.inflation_engine.config import (
    AggregationWeights,
    DEFAULT_AGGREGATION_WEIGHTS,
)
from backend.macro.inflation_engine.types import (
    Contribution,
    InflationScoreResult,
    LayerOutput,
)

logger = logging.getLogger(__name__)

# Layer names in canonical order (used for consistent dict ordering).
_LAYER_NAMES = ["structural", "momentum", "market_expectations", "surprise"]


def aggregate(
    layers: dict[str, LayerOutput],
    weights: Optional[AggregationWeights] = None,
) -> InflationScoreResult:
    """Combine layer outputs into a final InflationScoreResult.

    Parameters
    ----------
    layers:
        Dict mapping layer name → LayerOutput.  Expected keys:
        ``"structural"``, ``"momentum"``, ``"market_expectations"``,
        ``"surprise"``.  Any missing key is treated as confidence=0.
    weights:
        Base weights per layer.  Defaults to ``DEFAULT_AGGREGATION_WEIGHTS``
        (structural=0.40, momentum=0.25, market=0.25, surprise=0.10).

    Returns
    -------
    InflationScoreResult
        Full result including per-layer decomposition, top-5 contributors,
        stale warnings, and diagnostics.
    """
    if weights is None:
        weights = DEFAULT_AGGREGATION_WEIGHTS

    base_weights = weights.as_dict()

    # ── 1. Compute effective weights ─────────────────────────────────────────
    # effective_weight = base_weight × confidence
    effective: dict[str, float] = {}
    for name in _LAYER_NAMES:
        layer = layers.get(name)
        conf = layer.confidence if layer is not None else 0.0
        bw = base_weights.get(name, 0.0)
        effective[name] = bw * conf

    total_eff = sum(effective.values())

    # ── 2. Compute normalised weights and final score ─────────────────────────
    normalised: dict[str, float] = {}
    if total_eff == 0.0:
        # All layers have zero confidence — equal weighting fallback.
        active = [n for n in _LAYER_NAMES if layers.get(n) is not None]
        n_active = len(active)
        if n_active == 0:
            # No data at all.
            logger.warning("aggregate: no layers with data — returning score=0.0")
            return InflationScoreResult(
                score=0.0,
                confidence=0.0,
                layers=layers,
                contributors_top5=[],
                aggregate_method="static",
                stale_warnings=["all layers have zero confidence"],
                diagnostics={"base_weights": base_weights, "effective_weights": effective},
            )
        for name in active:
            normalised[name] = 1.0 / n_active
        for name in _LAYER_NAMES:
            if name not in normalised:
                normalised[name] = 0.0
        logger.debug("aggregate: all confidences zero — equal weighting fallback")
    else:
        for name in _LAYER_NAMES:
            normalised[name] = effective[name] / total_eff

    # Final score = Σ(layer.score × normalised_weight)
    score = 0.0
    for name in _LAYER_NAMES:
        layer = layers.get(name)
        if layer is not None and normalised[name] > 0:
            score += layer.score * normalised[name]

    score = max(-1.0, min(1.0, score))

    # ── 3. Aggregate confidence ───────────────────────────────────────────────
    # Aggregate confidence = weighted average of layer confidences (by base weight,
    # excluding layers with base_weight=0).
    total_bw = sum(base_weights[n] for n in _LAYER_NAMES)
    if total_bw > 0:
        agg_confidence = sum(
            (layers[n].confidence if n in layers else 0.0) * base_weights[n]
            for n in _LAYER_NAMES
        ) / total_bw
    else:
        agg_confidence = 0.0

    # ── 4. Collect stale warnings ─────────────────────────────────────────────
    stale_warnings: list[str] = []
    for name in _LAYER_NAMES:
        layer = layers.get(name)
        if layer is not None:
            stale_warnings.extend(layer.notes)

    # ── 5. Top-5 contributors by |signal × weight × confidence| ─────────────
    all_contributions: list[tuple[float, Contribution]] = []
    for name in _LAYER_NAMES:
        layer = layers.get(name)
        if layer is None:
            continue
        nw = normalised.get(name, 0.0)
        for contrib in layer.contributors:
            # Effective contribution magnitude = |signal × base_weight_within_layer × layer_norm_weight|
            magnitude = abs(contrib.signal * contrib.weight * nw)
            all_contributions.append((magnitude, contrib))

    all_contributions.sort(key=lambda x: x[0], reverse=True)
    top5 = [c for _, c in all_contributions[:5]]

    # ── 6. Diagnostics ────────────────────────────────────────────────────────
    diagnostics: dict = {
        "base_weights": base_weights,
        "effective_weights": {n: round(effective[n], 6) for n in _LAYER_NAMES},
        "normalised_weights": {n: round(normalised[n], 6) for n in _LAYER_NAMES},
        "layer_scores": {
            n: round(layers[n].score, 6) if n in layers else None
            for n in _LAYER_NAMES
        },
        "layer_confidences": {
            n: round(layers[n].confidence, 6) if n in layers else 0.0
            for n in _LAYER_NAMES
        },
    }

    logger.debug(
        "aggregate → score=%.4f  confidence=%.4f  "
        "norm_weights=%s  layer_scores=%s",
        score,
        agg_confidence,
        {n: f"{normalised[n]:.3f}" for n in _LAYER_NAMES},
        {n: f"{layers[n].score:.4f}" if n in layers else "N/A" for n in _LAYER_NAMES},
    )

    return InflationScoreResult(
        score=score,
        confidence=agg_confidence,
        layers=layers,
        contributors_top5=top5,
        aggregate_method="static",
        stale_warnings=stale_warnings,
        diagnostics=diagnostics,
    )
