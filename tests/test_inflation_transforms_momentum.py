"""Tests for transforms.momentum — pure time-series functions.

Coverage
--------
- annualised_3m: correct formula; None when <3 points; sign follows direction.
- regression_slope: positive when trend up; negative when down; None when too few.
- acceleration: positive when accelerating; negative when decelerating; None <3 pts.
- Edge cases: zero divisor, empty list, exactly-minimum length.
"""

from __future__ import annotations

import pytest

from backend.macro.inflation_engine.transforms.momentum import (
    acceleration,
    annualised_3m,
    regression_slope,
)


# ---------------------------------------------------------------------------
# annualised_3m
# ---------------------------------------------------------------------------


def test_annualised_3m_too_short_returns_none():
    assert annualised_3m([]) is None
    assert annualised_3m([1.0]) is None
    assert annualised_3m([1.0, 2.0]) is None


def test_annualised_3m_exactly_three():
    # 3 points: change = 3 - 1 = 2, annualised = 2 * (12/3) = 8
    result = annualised_3m([1.0, 2.0, 3.0])
    assert result == pytest.approx(8.0)


def test_annualised_3m_uses_last_three():
    # Series longer than 3; should use only last 3
    result = annualised_3m([10.0, 20.0, 1.0, 2.0, 3.0])
    assert result == pytest.approx(8.0)


def test_annualised_3m_positive_acceleration():
    result = annualised_3m([2.0, 3.0, 4.0])  # trending up
    assert result is not None
    assert result > 0


def test_annualised_3m_negative_deceleration():
    result = annualised_3m([4.0, 3.0, 2.0])  # trending down
    assert result is not None
    assert result < 0


def test_annualised_3m_flat_near_zero():
    result = annualised_3m([2.0, 2.0, 2.0])
    assert result == pytest.approx(0.0, abs=1e-9)


def test_annualised_3m_zero_base_returns_none():
    # v[-3] = 0 → division by zero case
    result = annualised_3m([0.0, 1.0, 2.0])
    # Our implementation: if v_3m_ago == 0, return None
    assert result is None


def test_annualised_3m_formula_direct():
    """Direct check: (v[-1] - v[-3]) * 4."""
    # annualised 3m = (last - 3m_ago) * (12/3) = delta * 4
    series = [1.5, 2.0, 2.5]
    result = annualised_3m(series)
    # change = 2.5 - 1.5 = 1.0, annualised = 1.0 * 4 = 4.0
    assert result == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# regression_slope
# ---------------------------------------------------------------------------


def test_regression_slope_too_short():
    assert regression_slope([]) is None
    assert regression_slope([1.0, 2.0, 3.0], n=6) is None  # need 6


def test_regression_slope_exact_n():
    # Perfectly linear series: slope = 1.0 per period
    series = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    result = regression_slope(series, n=6)
    assert result == pytest.approx(1.0, abs=1e-9)


def test_regression_slope_declining_series():
    series = [6.0, 5.0, 4.0, 3.0, 2.0, 1.0]
    result = regression_slope(series, n=6)
    assert result is not None
    assert result < 0


def test_regression_slope_flat_near_zero():
    series = [3.0] * 6
    result = regression_slope(series, n=6)
    # Flat series — variance of x is non-zero but cov(x,y)=0
    assert result == pytest.approx(0.0, abs=1e-9)


def test_regression_slope_uses_last_n():
    # Series longer than n; slope computed from last 6
    series = [100.0, 200.0, 300.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    result = regression_slope(series, n=6)
    assert result == pytest.approx(1.0, abs=1e-9)


def test_regression_slope_default_n_6():
    series = list(range(10))  # 0..9, slope = 1
    result = regression_slope(series)  # default n=6
    assert result is not None


def test_regression_slope_non_integer_result():
    # Noisy series: we just check sign and finiteness
    import math
    series = [2.1, 2.4, 2.3, 2.5, 2.7, 2.8]
    result = regression_slope(series, n=6)
    assert result is not None
    assert math.isfinite(result)
    assert result > 0  # generally trending up


# ---------------------------------------------------------------------------
# acceleration
# ---------------------------------------------------------------------------


def test_acceleration_too_short():
    assert acceleration([]) is None
    assert acceleration([1.0]) is None
    assert acceleration([1.0, 2.0]) is None


def test_acceleration_exactly_three():
    # series = [1, 2, 4]: first diff = [1, 2]; second diff = 1 (accelerating)
    result = acceleration([1.0, 2.0, 4.0])
    assert result == pytest.approx(1.0)


def test_acceleration_uses_last_three():
    # Only last 3 values matter
    result = acceleration([100.0, 99.0, 1.0, 2.0, 4.0])
    assert result == pytest.approx(1.0)


def test_acceleration_positive_accelerating():
    # Each step is larger than the last → positive acceleration
    result = acceleration([2.0, 3.0, 5.0])
    assert result is not None
    assert result > 0


def test_acceleration_negative_decelerating():
    # Each step is smaller → negative acceleration (decelerating)
    result = acceleration([5.0, 4.0, 2.0])
    assert result is not None
    assert result < 0


def test_acceleration_linear_is_zero():
    # Constant rate of change → second difference = 0
    result = acceleration([1.0, 2.0, 3.0])
    assert result == pytest.approx(0.0, abs=1e-9)


def test_acceleration_formula():
    """a = series[-1] - 2*series[-2] + series[-3]."""
    s = [3.0, 5.0, 6.0]
    result = acceleration(s)
    expected = 6.0 - 2 * 5.0 + 3.0  # = -1
    assert result == pytest.approx(expected)
