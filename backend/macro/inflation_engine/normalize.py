"""Continuous normalization primitives for the inflation engine.

Every function in this module returns a value in [-1.0, +1.0]. They replace the
discrete 5-step buckets (`-1, -0.5, 0, +0.5, +1`) used by the legacy scorer.

Why continuous over discrete:
  - A CPI YoY move from 3.0 → 2.9 changes a step-function output from 0.5 → 0.0
    (a 50 % swing from a 10 bp data move). Tanh maps the same input to a ~2 bp
    swing — preserving information rather than discarding it.
  - Step functions accumulate threshold sensitivity at boundaries and become
    completely insensitive elsewhere; tanh is smooth and monotone everywhere.

All transforms are pure (no I/O, no globals).
"""

from __future__ import annotations

import math
from typing import Iterable, Optional

import numpy as np


__all__ = [
    "tanh_norm",
    "rolling_zscore",
    "percentile_rank",
    "winsorize",
    "vol_adjusted",
]


def tanh_norm(value: float, center: float, scale: float) -> float:
    """Smooth bounded soft-clip via the hyperbolic tangent.

    Formula::

        score = tanh((value - center) / scale)

    Useful when the indicator has an economically meaningful anchor (e.g. Fed's
    2 % CPI target) and a characteristic deviation that should map to ~tanh(1) ≈
    +0.76. Outside ±3 × scale, the output saturates near ±1 without clipping
    discontinuously — preserving directionality at the extremes.

    Args:
        value:  Raw observation, e.g. CPI YoY = 3.5 %.
        center: Anchor — the value that maps to 0.0 (e.g. 2.0 for Fed target).
        scale:  Characteristic deviation. ±1×scale ≈ ±0.76, ±2×scale ≈ ±0.96.

    Returns:
        A float in [-1, +1]. Always finite.

    Raises:
        ValueError: If ``scale`` is non-positive.
    """
    if scale <= 0:
        raise ValueError(f"tanh_norm: scale must be > 0, got {scale}")
    if not math.isfinite(value):
        raise ValueError(f"tanh_norm: value must be finite, got {value}")
    return math.tanh((value - center) / scale)


def rolling_zscore(
    value: float,
    history: Iterable[float],
    *,
    bound: bool = True,
    bound_scale: float = 2.5,
    min_history: int = 8,
) -> Optional[float]:
    """Z-score against a rolling window, optionally tanh-bounded to [-1, +1].

    Distance-from-recent-regime metric. Robust to absolute level — useful when
    the indicator drifts over years (e.g. core CPI in different policy regimes)
    and what matters is "is the current print unusual *relative to recent
    history*".

    Args:
        value:        Current observation.
        history:      Past observations (any iterable). The current value should
                      NOT be included — pass historical context only.
        bound:        If True (default), bound the z-score to [-1, +1] via
                      ``tanh(z / bound_scale)``. If False, return raw z.
        bound_scale:  Tanh divisor when bounding. 2.5 means a ±2.5 σ move maps
                      to ~±tanh(1) ≈ ±0.76.
        min_history:  Minimum history length required. Returns None if not met.

    Returns:
        Bounded score in [-1, +1] (or raw z if ``bound=False``). None when
        history is too short or has zero variance.
    """
    arr = np.asarray(list(history), dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size < min_history:
        return None
    mu = float(arr.mean())
    sigma = float(arr.std(ddof=1)) if arr.size > 1 else 0.0
    if sigma == 0.0:
        return None
    z = (value - mu) / sigma
    if not bound:
        return z
    return math.tanh(z / bound_scale)


def percentile_rank(
    value: float,
    history: Iterable[float],
    *,
    min_history: int = 8,
) -> Optional[float]:
    """Empirical percentile rank, mapped to [-1, +1].

    Distribution-free — ignores absolute level and even shape of the
    distribution, only how the current value ranks against history. Excellent
    for indicators with fat tails or non-normal distributions (e.g. PPI spikes).

    Args:
        value:       Current observation.
        history:     Past observations (current value should NOT be included).
        min_history: Minimum history length required.

    Returns:
        Score in [-1, +1]:
            -1.0 → at or below the historical minimum
            +1.0 → at or above the historical maximum
             0.0 → exactly the historical median
        None when history is too short.
    """
    arr = np.asarray(list(history), dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size < min_history:
        return None
    # Fraction of history below `value`, with ties contributing 0.5 each.
    below = float(np.sum(arr < value))
    ties = float(np.sum(arr == value))
    pct = (below + 0.5 * ties) / arr.size
    # Map [0, 1] → [-1, +1].
    return 2.0 * pct - 1.0


def winsorize(
    value: float,
    history: Iterable[float],
    *,
    p_low: float = 0.05,
    p_high: float = 0.95,
    min_history: int = 8,
) -> Optional[float]:
    """Clip ``value`` to the empirical [p_low, p_high] quantiles of history.

    Useful as a pre-processing step before another normalization to dampen
    impact of one-off extreme prints (data errors, holiday distortions, etc.)
    while preserving the sign of the move.

    Args:
        value:       Current observation.
        history:     Past observations (current value should NOT be included).
        p_low:       Lower quantile (default 5th percentile).
        p_high:      Upper quantile (default 95th percentile).
        min_history: Minimum history length required.

    Returns:
        Clipped raw value (NOT remapped to [-1, +1] — this is a pre-processor).
        None when history is too short.

    Raises:
        ValueError: If quantile bounds are inverted or out of [0, 1].
    """
    if not (0.0 <= p_low < p_high <= 1.0):
        raise ValueError(
            f"winsorize: invalid quantile bounds p_low={p_low} p_high={p_high}"
        )
    arr = np.asarray(list(history), dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size < min_history:
        return None
    lo = float(np.quantile(arr, p_low))
    hi = float(np.quantile(arr, p_high))
    return float(min(max(value, lo), hi))


def vol_adjusted(
    value: float,
    history: Iterable[float],
    *,
    bound_scale: float = 2.5,
    min_history: int = 8,
) -> Optional[float]:
    """Scale ``value`` by realised volatility of history, then bound via tanh.

    Useful when comparing indicators with different scales (e.g. CPI YoY in
    percent vs. yield in basis points): dividing by the indicator's own σ puts
    them on a comparable footing.

    Differs from ``rolling_zscore`` in that it does NOT subtract the mean —
    use this when zero is an economically meaningful anchor (e.g. ROC, MoM
    change) and the centring should come from the data itself, not from a
    rolling baseline.

    Args:
        value:       Current observation (typically a change, e.g. ΔYoY).
        history:     Past observations of the same series.
        bound_scale: Tanh divisor. Larger = less aggressive bounding.
        min_history: Minimum history length required.

    Returns:
        Bounded score in [-1, +1]. None when history is too short or has
        zero variance.
    """
    arr = np.asarray(list(history), dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size < min_history:
        return None
    sigma = float(arr.std(ddof=1)) if arr.size > 1 else 0.0
    if sigma == 0.0:
        return None
    return math.tanh((value / sigma) / bound_scale)
