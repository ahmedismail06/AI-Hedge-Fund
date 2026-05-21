"""Surprise (actual-vs-consensus) inflation layer.

PR 4 of the inflation engine redesign (docs/inflation_engine_design.md §2.5).

Computes a score based on the deviation of actual prints from consensus
expectations.  Only releases with both ``actual_value`` and ``expected_value``
populated are scored.

Scoring formula::

    surprise = actual - expected
    signal = tanh(surprise / scale)

Weights are equal across all scored indicators in the snapshot.
Confidence is 1.0 if there is at least one fresh surprise, decaying with age.
"""

from __future__ import annotations

import logging
from typing import List

from backend.macro.inflation_engine.normalize import tanh_norm
from backend.macro.inflation_engine.types import (
    Contribution,
    InflationSnapshot,
    LayerOutput,
)

logger = logging.getLogger(__name__)

# Characteristic scale for surprises (in percentage points).
# 0.20 means a 20bp surprise maps to tanh(1) ≈ 0.76.
# 10bp surprise maps to tanh(0.5) ≈ 0.46.
SURPRISE_SCALE = 0.20


def compute(snapshot: InflationSnapshot) -> LayerOutput:
    """Compute the surprise layer score from actual-vs-consensus prints.

    Parameters
    ----------
    snapshot:
        InflationSnapshot containing a list of ``expectations``.

    Returns
    -------
    LayerOutput
        Score in [-1, +1]. Confidence is 0.0 if no surprises are available.
    """
    # 1. Filter for releases that have both actual and expected values.
    active_surprises = [
        exp for exp in snapshot.expectations
        if exp.actual_value is not None and exp.expected_value is not None
    ]

    if not active_surprises:
        return LayerOutput(
            score=0.0,
            confidence=0.0,
            contributors=[],
            notes=["no actual-vs-consensus data available"],
        )

    # 2. Score each surprise.
    contributors: List[Contribution] = []
    total_score = 0.0

    # We weight all surprises equally for now.
    weight = 1.0 / len(active_surprises)

    for exp in active_surprises:
        surprise = exp.actual_value - exp.expected_value
        signal = tanh_norm(surprise, center=0.0, scale=SURPRISE_SCALE)

        # Surprise confidence decays with age.  A surprise is "fresh" for 7 days,
        # then decays. (Simple implementation for now).
        # In a real system, we might want to only count the *latest* surprise
        # for each indicator.

        # For PR4, we'll use a simple 1.0 confidence for any surprise in the snapshot,
        # assuming the ingestion layer only provided relevant recent ones.
        conf = 1.0

        contributors.append(
            Contribution(
                indicator=f"surprise_{exp.indicator}",
                raw_value=surprise,
                signal=signal,
                weight=weight,
                confidence=conf,
                age_days=None,  # Not tracked here, release_dt is in exp
            )
        )
        total_score += signal * weight

    # Aggregate confidence: if we have any active surprises, we have confidence.
    # In PR4, we just return 1.0 if any exist, as the surprise layer is a high-alpha
    # but low-frequency signal.

    return LayerOutput(
        score=total_score,
        confidence=1.0,
        contributors=contributors,
        notes=[f"scored {len(active_surprises)} surprises"],
    )

