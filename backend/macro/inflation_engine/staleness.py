"""Staleness → confidence mapping for mixed-frequency indicator aggregation.

PR 2 of the inflation engine redesign (docs/inflation_engine_design.md §2.3).

Why a separate module rather than adding to normalize.py:
  - normalize.py contains *signal-space* transforms (tanh, z-score, percentile
    rank). Those are pure math on indicator values.
  - staleness.py operates on *time-space* metadata (how old is this series?).
    Mixing the two concerns would make normalize.py ambiguous to future readers.

Confidence formula
------------------
For each native frequency tier we use an exponential decay:

    confidence(staleness_days) = exp(-staleness_days / halflife)

Halflife choices (motivated by the design doc targets in §2.3):

    daily     — halflife = 7 days
        At 0 days  → 1.0
        At 1 day   → 0.87
        At 5 days  → 0.49  (≈ 0.5 target)
        At 14 days → 0.14  (approaching 0 target)

    weekly    — halflife = 14 days
        At 0 days  → 1.0
        At 7 days  → 0.61
        At 21 days → 0.22

    monthly   — halflife = 45 days
        At 0 days  → 1.0
        At 21 days → 0.63  (≈ 0.7 target)
        At 35 days → 0.46  (≈ 0.4 target)
        At 45 days → 0.37
        At 60 days → 0.26  (late release, keeps dropping)

    quarterly — halflife = 90 days
        At 0 days   → 1.0
        At 45 days  → 0.61
        At 90 days  → 0.37

The output is clipped to [0.0, 1.0] (exp is always positive so only the
upper clip ever activates as a guard against floating-point edge cases).

Functions are pure (no I/O, no globals) and fully deterministic.
"""

from __future__ import annotations

import math

from backend.macro.inflation_engine.types import IndicatorMeta, NativeFrequency


__all__ = [
    "HALFLIVES",
    "staleness_confidence",
    "confidence_from_meta",
]


# Halflife in days per native frequency.
# Chosen to satisfy the design doc's approximate targets while remaining
# a smooth, monotone function with no discontinuities.
HALFLIVES: dict[NativeFrequency, float] = {
    "daily":     7.0,
    "weekly":    14.0,
    "monthly":   45.0,
    "quarterly": 90.0,
}


def staleness_confidence(staleness_days: int | float, native_frequency: NativeFrequency) -> float:
    """Map staleness to a confidence weight in [0, 1] for one indicator.

    Formula::

        confidence = exp(-staleness_days / halflife[native_frequency])

    Parameters
    ----------
    staleness_days:
        Number of days since the indicator's last observed release.
        Must be ≥ 0.
    native_frequency:
        The indicator's publication cadence — determines which halflife to use.

    Returns
    -------
    float
        A value in [0.0, 1.0].
        - 1.0 when the series is perfectly fresh (staleness = 0).
        - Approaches 0.0 asymptotically as staleness grows.
        - Smooth and strictly monotone decreasing.

    Raises
    ------
    ValueError
        If ``staleness_days`` is negative or ``native_frequency`` is unknown.
    """
    if staleness_days < 0:
        raise ValueError(
            f"staleness_confidence: staleness_days must be ≥ 0, got {staleness_days}"
        )
    halflife = HALFLIVES.get(native_frequency)
    if halflife is None:
        raise ValueError(
            f"staleness_confidence: unknown native_frequency '{native_frequency}'. "
            f"Must be one of {list(HALFLIVES)}."
        )
    raw = math.exp(-float(staleness_days) / halflife)
    # Clip to [0, 1] as a guard against floating-point underflow/overflow
    return max(0.0, min(1.0, raw))


def confidence_from_meta(meta: IndicatorMeta) -> float:
    """Convenience wrapper: compute confidence directly from an IndicatorMeta.

    Parameters
    ----------
    meta:
        Populated IndicatorMeta for a single indicator.

    Returns
    -------
    float
        Confidence weight in [0.0, 1.0].
    """
    return staleness_confidence(meta.staleness_days, meta.native_frequency)
