"""Structural (level-based) inflation layer.

PR 3 of the inflation engine redesign (docs/inflation_engine_design.md §2.1).

This layer captures the *level* of inflation pressure using YoY series anchored
at the Fed's 2% target.  It is intentionally slow-moving — it anchors the
engine's long-run view and prevents the momentum and market layers from
overreacting to transient noise.

Indicator weights (sum to 1.0):
    CPI YoY            30%
    Core CPI YoY       30%
    PPI YoY            15%
    PCE YoY            15%
    Sticky-price CPI    5%
    Trimmed-mean PCE    5%

Confidence weighting:
    Each indicator's effective weight = base_weight × staleness_confidence.
    Weights are then renormalised to sum to 1.0.
    If no indicators are present, LayerOutput(score=0.0, confidence=0.0).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from backend.macro.inflation_engine import config as cfg
from backend.macro.inflation_engine.normalize import tanh_norm
from backend.macro.inflation_engine.staleness import staleness_confidence
from backend.macro.inflation_engine.types import (
    Contribution,
    InflationSnapshot,
    LayerOutput,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Layer configuration — (key, base_weight, tanh_params, native_frequency)
# ---------------------------------------------------------------------------

_STRUCTURAL_SPECS: list[tuple[str, float, cfg.TanhParams, str]] = [
    ("cpi_yoy",           0.30, cfg.CPI_YOY,             "monthly"),
    ("core_cpi_yoy",      0.30, cfg.CORE_CPI_YOY,         "monthly"),
    ("ppi_yoy",           0.15, cfg.PPI_YOY,              "monthly"),
    ("pce_yoy",           0.15, cfg.PCE_YOY,              "monthly"),
    ("sticky_cpi_yoy",    0.05, cfg.STICKY_CPI_YOY,       "monthly"),
    ("trimmed_mean_pce_yoy", 0.05, cfg.TRIMMED_MEAN_PCE_YOY, "monthly"),
]


def compute(snapshot: InflationSnapshot) -> LayerOutput:
    """Compute the structural inflation layer score.

    Parameters
    ----------
    snapshot:
        InflationSnapshot for the current scoring run.

    Returns
    -------
    LayerOutput
        ``score`` in [-1, +1], ``confidence`` in [0, 1].
        ``confidence`` = weighted-average freshness confidence across present indicators.
        If no indicators are present, returns score=0.0, confidence=0.0.
    """
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    contributors: list[Contribution] = []
    notes: list[str] = []

    # Collect (signal, base_weight, confidence, Contribution) tuples.
    scored: list[tuple[float, float, float]] = []  # (signal, base_weight, confidence)

    for key, base_weight, params, native_freq in _STRUCTURAL_SPECS:
        value: Optional[float] = getattr(snapshot, key, None)
        if value is None:
            notes.append(f"structural: {key} missing — skipped")
            logger.debug("structural/%s: missing", key)
            continue

        signal = tanh_norm(value, params.center, params.scale)

        # Determine confidence from release_dates if available.
        release_dt = snapshot.release_dates.get(key)
        if release_dt is not None:
            staleness = max(0, (today - release_dt).days)
            conf = staleness_confidence(staleness, native_freq)  # type: ignore[arg-type]
            age_days: Optional[int] = staleness
        else:
            # No release date: apply conservative fallback (1 full native period elapsed).
            from backend.macro.inflation_engine.types import FREQUENCY_DAYS
            fallback_staleness = FREQUENCY_DAYS[native_freq]
            conf = staleness_confidence(fallback_staleness, native_freq)  # type: ignore[arg-type]
            age_days = None
            notes.append(f"structural: {key} no release_date → fallback staleness={fallback_staleness}d")

        contrib = Contribution(
            indicator=key,
            raw_value=value,
            signal=signal,
            weight=base_weight,
            confidence=conf,
            age_days=age_days,
        )
        contributors.append(contrib)
        scored.append((signal, base_weight, conf))

        logger.debug(
            "structural/%s=%.3f → signal=%.4f  weight=%.2f  conf=%.3f",
            key, value, signal, base_weight, conf,
        )

    if not scored:
        logger.debug("structural: no indicators available → score=0.0, confidence=0.0")
        return LayerOutput(
            score=0.0,
            confidence=0.0,
            contributors=[],
            notes=notes + ["structural: no indicators available"],
        )

    # Effective weight = base_weight × confidence; renormalise to sum=1.
    effective_weights = [bw * c for _, bw, c in scored]
    total_eff = sum(effective_weights)

    # Total base weight across ALL expected structural indicators.
    total_possible_bw = sum(bw for _, bw, _, _ in _STRUCTURAL_SPECS)

    if total_eff == 0.0:
        # All inputs have zero confidence — unweighted mean fallback.
        score = sum(s for s, _, _ in scored) / len(scored)
        layer_confidence = 0.0
        notes.append("structural: all confidences zero — unweighted mean fallback")
    else:
        score = sum(s * ew for (s, _, _), ew in zip(scored, effective_weights)) / total_eff
        # Layer confidence = sum(present_bw × freshness_conf) / total_possible_bw.
        # Accounts for both freshness decay and missing-indicator coverage.
        layer_confidence = sum(c * bw for _, bw, c in scored) / total_possible_bw

    # Clamp to [-1, +1] (tanh is already bounded, but guard against fp rounding).
    score = max(-1.0, min(1.0, score))

    logger.debug(
        "structural → score=%.4f  confidence=%.4f  n_indicators=%d",
        score, layer_confidence, len(scored),
    )

    return LayerOutput(
        score=score,
        confidence=layer_confidence,
        contributors=contributors,
        notes=notes,
    )
