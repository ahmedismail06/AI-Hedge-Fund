"""Momentum (rate-of-change) inflation layer.

PR 3 of the inflation engine redesign (docs/inflation_engine_design.md §2.1).

This layer captures *how fast* inflation is accelerating or decelerating.
It is intentionally forward-leaning and more sensitive than the structural
layer — it can turn negative before the YoY series has visibly peaked.

Inputs (all pre-computed by transforms.momentum and stored in InflationSnapshot):
    cpi_yoy_3m:     3m-annualised CPI momentum
    core_cpi_yoy_3m: 3m-annualised core CPI momentum
    cpi_slope_6m:   6m OLS regression slope of CPI YoY

Normalisation anchors:
    - 3m-annualised series: center=0.0, scale=2.0
        0 pp ann → 0.0 (flat)
        +1 pp ann → tanh(0.5) ≈ +0.46  (accelerating)
        +2 pp ann → tanh(1.0) ≈ +0.76  (strongly accelerating)
        -1 pp ann → tanh(-0.5) ≈ -0.46 (decelerating)
        Design choice: scale=2.0 means a 1 pp annualised change maps to ≈ 0.46.
        This is deliberate — momentum is a secondary signal; we don't want it
        dominating the score on a single month's print.

    - 6m slope: center=0.0, scale=0.3 (pp per month)
        ±0.15 pp/month (≈ ±1.8 pp ann) → ±tanh(0.5) ≈ ±0.46

Confidence:
    1.0 when all three inputs are present.
    Degrades proportionally: 2/3 when one is missing, 1/3 when two are missing,
    0.0 when all are missing.
"""

from __future__ import annotations

import logging

from backend.macro.inflation_engine import config as cfg
from backend.macro.inflation_engine.normalize import tanh_norm
from backend.macro.inflation_engine.types import (
    Contribution,
    InflationSnapshot,
    LayerOutput,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Layer configuration
# ---------------------------------------------------------------------------

_MOMENTUM_SPECS: list[tuple[str, cfg.TanhParams]] = [
    ("cpi_yoy_3m",      cfg.MOMENTUM_3M),
    ("core_cpi_yoy_3m", cfg.MOMENTUM_3M),
    ("cpi_slope_6m",    cfg.MOMENTUM_SLOPE_6M),
]

_N_INPUTS = len(_MOMENTUM_SPECS)  # 3


def compute(snapshot: InflationSnapshot) -> LayerOutput:
    """Compute the momentum inflation layer score.

    Parameters
    ----------
    snapshot:
        InflationSnapshot for the current scoring run.

    Returns
    -------
    LayerOutput
        ``score`` in [-1, +1].
        ``confidence`` = fraction of expected inputs that are present.
        0.0 confidence when no momentum inputs are available.
    """
    contributors: list[Contribution] = []
    notes: list[str] = []
    signals: list[float] = []

    for key, params in _MOMENTUM_SPECS:
        value = getattr(snapshot, key, None)
        if value is None:
            notes.append(f"momentum: {key} missing — skipped")
            logger.debug("momentum/%s: missing", key)
            continue

        signal = tanh_norm(value, params.center, params.scale)
        # Equal weighting — all three inputs are the same series (CPI) at
        # different transform lags; giving them equal weight avoids double-
        # counting any single transform.
        base_weight = 1.0 / _N_INPUTS

        contrib = Contribution(
            indicator=key,
            raw_value=value,
            signal=signal,
            weight=base_weight,
            confidence=1.0,  # momentum inputs are always "fresh" when present
            age_days=None,
        )
        contributors.append(contrib)
        signals.append(signal)

        logger.debug(
            "momentum/%s=%.4f → signal=%.4f",
            key, value, signal,
        )

    if not signals:
        logger.debug("momentum: no inputs available → score=0.0, confidence=0.0")
        return LayerOutput(
            score=0.0,
            confidence=0.0,
            contributors=[],
            notes=notes + ["momentum: no inputs available"],
        )

    # Simple average of available signals.
    score = sum(signals) / len(signals)
    # Confidence = fraction of expected inputs present.
    confidence = len(signals) / _N_INPUTS

    score = max(-1.0, min(1.0, score))

    logger.debug(
        "momentum → score=%.4f  confidence=%.4f  n_inputs=%d/%d",
        score, confidence, len(signals), _N_INPUTS,
    )

    return LayerOutput(
        score=score,
        confidence=confidence,
        contributors=contributors,
        notes=notes,
    )
