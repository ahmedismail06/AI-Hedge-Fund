"""Momentum transforms for the inflation scoring engine.

PR 3 of the inflation engine redesign (docs/inflation_engine_design.md §3).

All functions are pure: no I/O, no globals, deterministic for the same inputs.
They consume lists of monthly time-series values and return floats (or None
when there is insufficient history).

The outputs of these functions are consumed by:
  - ``layers/momentum.py`` (the momentum scoring layer)
  - ``ingestion/fred_inflation.py`` (optionally pre-populates InflationSnapshot)

Functions
---------
annualised_3m:     3m-annualised rate of change from the last 3 monthly values.
regression_slope:  OLS slope over the last n points (default 6 months).
acceleration:      Second-difference (change-of-change) from the last 3 values.
"""

from __future__ import annotations

from typing import Optional

import numpy as np


__all__ = [
    "annualised_3m",
    "regression_slope",
    "acceleration",
]


def annualised_3m(series: list[float]) -> Optional[float]:
    """Convert the last 3 monthly observations to an annualised rate.

    Formula::

        3m_annualised = ((v[-1] / v[-3]) - 1) * 12 / 3 * 100

    For YoY series (e.g. CPI YoY), this gives the annualised change-in-change:
    how fast is the YoY rate itself accelerating or decelerating over the
    last 3 months.

    For level series (e.g. raw CPI index), this gives the annualised rate of
    price change implied by the last 3 months.

    Scale note: a 1 pp annualised change (e.g. CPI 3m-ann moves from 2.0 to 3.0)
    maps to approximately tanh(0.5) ≈ 0.46 in the momentum layer using
    MOMENTUM_3M (center=0, scale=2.0).

    Parameters
    ----------
    series:
        List of monthly values in chronological order. At least 3 values required.

    Returns
    -------
    float | None
        Annualised rate (pp per year) or None if fewer than 3 values.
    """
    if len(series) < 3:
        return None
    v_now = series[-1]
    v_3m_ago = series[-3]
    if v_3m_ago == 0:
        return None
    # Change over 3 months, annualised to 12 months
    change_3m = v_now - v_3m_ago
    annualised = change_3m * (12.0 / 3.0)
    return float(annualised)


def regression_slope(series: list[float], n: int = 6) -> Optional[float]:
    """OLS slope of the last ``n`` points against a time index.

    Fits a simple linear regression  y ~ a + b*t  where t = 0, 1, …, n-1.
    Returns the slope ``b``, which represents the average change per period
    (month) over the trailing window.

    Use case: captures whether CPI YoY is trending up or down over 6 months,
    filtering out single-month noise.

    Scale note: a slope of ±0.15 pp/month (≈ ±1.8 pp annualised) maps to
    approximately tanh(±0.5) ≈ ±0.46 using MOMENTUM_SLOPE_6M (scale=0.3).

    Parameters
    ----------
    series:
        List of monthly values in chronological order.
    n:
        Number of trailing observations to use. Default 6 months.

    Returns
    -------
    float | None
        OLS slope in original units per month, or None if ``len(series) < n``.
    """
    if len(series) < n:
        return None
    y = np.asarray(series[-n:], dtype=float)
    x = np.arange(n, dtype=float)
    # Standard OLS: slope = cov(x, y) / var(x)
    x_mean = x.mean()
    y_mean = y.mean()
    num = float(np.sum((x - x_mean) * (y - y_mean)))
    den = float(np.sum((x - x_mean) ** 2))
    if den == 0:
        return None
    return float(num / den)


def acceleration(series: list[float]) -> Optional[float]:
    """Second difference of the last 3 values: change-of-change.

    Formula::

        accel = (series[-1] - series[-2]) - (series[-2] - series[-3])
               = series[-1] - 2*series[-2] + series[-3]

    Positive: the series is accelerating upward (convex).
    Negative: the series is decelerating (concave — e.g. disinflation).
    Zero:     linear trend (constant monthly change).

    Parameters
    ----------
    series:
        List of monthly values in chronological order. At least 3 required.

    Returns
    -------
    float | None
        Second difference in original units, or None if fewer than 3 values.
    """
    if len(series) < 3:
        return None
    return float(series[-1] - 2 * series[-2] + series[-3])
