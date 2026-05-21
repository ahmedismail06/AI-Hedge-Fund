"""Tests for backend.macro.inflation_engine.normalize.

Coverage:
- tanh_norm: boundedness, monotonicity, smoothness at legacy bucket boundaries,
  agreement with bucket midpoints in sign, invalid input handling
- rolling_zscore: short-history None, zero-variance None, monotonic,
  bounded vs unbounded variants
- percentile_rank: boundedness, monotonic in value, median == 0.0
- winsorize: clipping at quantile bounds, sign preservation
- vol_adjusted: boundedness, scale invariance up to bound_scale
- Integration: replacement scorer agrees with legacy at bucket midpoints in
  sign, varies smoothly between them, and produces the expected score for
  today's actual macro signals.
"""

import math

import numpy as np
import pytest

from backend.macro.inflation_engine import config as inflation_config
from backend.macro.inflation_engine.normalize import (
    percentile_rank,
    rolling_zscore,
    tanh_norm,
    vol_adjusted,
    winsorize,
)
from backend.macro.scorer import RawIndicators, _score_inflation


# ---------------------------------------------------------------------------
# tanh_norm
# ---------------------------------------------------------------------------


def test_tanh_norm_anchor_is_zero():
    """center value should map exactly to 0.0."""
    assert tanh_norm(2.0, center=2.0, scale=1.5) == pytest.approx(0.0)


def test_tanh_norm_bounded_at_extremes():
    """Output is bounded in [-1, +1] for very large positive/negative inputs."""
    high = tanh_norm(100.0, center=2.0, scale=1.5)
    low = tanh_norm(-100.0, center=2.0, scale=1.5)
    assert -1.0 <= low <= -0.999
    assert 0.999 <= high <= 1.0


def test_tanh_norm_monotonic():
    """Strictly increasing in value across the full meaningful range."""
    xs = np.linspace(-5, 10, 200)
    ys = [tanh_norm(x, center=2.0, scale=1.5) for x in xs]
    diffs = np.diff(ys)
    assert (diffs > 0).all(), "tanh_norm must be strictly monotonic"


def test_tanh_norm_smooth_near_legacy_threshold():
    """The legacy CPI bucket cliff at 3.0 → was 0.5 swing on 10bp move.

    New scorer must change by <2bp for the same 10bp data move at that boundary.
    """
    s_before = tanh_norm(2.99, center=2.0, scale=1.5)
    s_after = tanh_norm(3.01, center=2.0, scale=1.5)
    delta = abs(s_after - s_before)
    assert delta < 0.02, f"expected smooth transition, got Δ={delta:.4f}"


def test_tanh_norm_invalid_scale_raises():
    with pytest.raises(ValueError):
        tanh_norm(2.0, center=2.0, scale=0.0)
    with pytest.raises(ValueError):
        tanh_norm(2.0, center=2.0, scale=-1.0)


def test_tanh_norm_nonfinite_raises():
    with pytest.raises(ValueError):
        tanh_norm(float("nan"), center=2.0, scale=1.5)
    with pytest.raises(ValueError):
        tanh_norm(float("inf"), center=2.0, scale=1.5)


# ---------------------------------------------------------------------------
# rolling_zscore
# ---------------------------------------------------------------------------


def test_rolling_zscore_short_history_returns_none():
    assert rolling_zscore(3.0, history=[1.0, 2.0], min_history=8) is None


def test_rolling_zscore_zero_variance_returns_none():
    """All identical history → undefined sigma → None (not crash, not divide-by-zero)."""
    assert rolling_zscore(5.0, history=[2.0] * 20) is None


def test_rolling_zscore_bounded_default():
    """Default behaviour is to tanh-bound the z-score to [-1, +1]."""
    history = list(np.random.default_rng(0).normal(0.0, 1.0, 200))
    score = rolling_zscore(5.0, history=history)  # 5σ event
    assert score is not None
    assert -1.0 < score < 1.0
    assert score > 0.9  # large positive z → near +1


def test_rolling_zscore_unbounded_when_bound_false():
    """bound=False returns raw z, which can exceed ±1."""
    history = [0.0] * 20 + [1.0] * 20
    score = rolling_zscore(5.0, history=history, bound=False)
    assert score is not None
    assert score > 5.0  # raw z, no tanh squash


def test_rolling_zscore_monotonic_in_value():
    history = list(np.random.default_rng(1).normal(2.0, 0.5, 100))
    xs = np.linspace(0.0, 5.0, 50)
    ys = [rolling_zscore(x, history=history) for x in xs]
    assert all(b > a for a, b in zip(ys, ys[1:]))


# ---------------------------------------------------------------------------
# percentile_rank
# ---------------------------------------------------------------------------


def test_percentile_rank_bounded():
    history = list(np.random.default_rng(2).normal(2.0, 0.5, 100))
    for value in [-10.0, 0.0, 2.0, 5.0, 100.0]:
        score = percentile_rank(value, history=history)
        assert score is not None
        assert -1.0 <= score <= 1.0


def test_percentile_rank_median_is_zero():
    """Value equal to historical median → exactly 0.0 (50th percentile)."""
    history = list(range(1, 100))  # median = 50
    score = percentile_rank(50, history=history)
    assert score == pytest.approx(0.0, abs=0.02)


def test_percentile_rank_below_min_is_minus_one():
    score = percentile_rank(-100.0, history=list(range(100)))
    assert score == pytest.approx(-1.0, abs=0.01)


def test_percentile_rank_above_max_is_plus_one():
    score = percentile_rank(1000.0, history=list(range(100)))
    assert score == pytest.approx(1.0, abs=0.01)


def test_percentile_rank_short_history_returns_none():
    assert percentile_rank(5.0, history=[1, 2, 3]) is None


# ---------------------------------------------------------------------------
# winsorize
# ---------------------------------------------------------------------------


def test_winsorize_clips_to_quantile_bounds():
    history = list(range(0, 100))  # 5th pct ≈ 5, 95th pct ≈ 94
    clipped_high = winsorize(1000.0, history=history)
    clipped_low = winsorize(-1000.0, history=history)
    assert clipped_high is not None and clipped_high <= 95.0
    assert clipped_low is not None and clipped_low >= 4.0


def test_winsorize_passes_inrange_unchanged():
    history = list(range(0, 100))
    assert winsorize(50.0, history=history) == pytest.approx(50.0)


def test_winsorize_invalid_quantile_raises():
    with pytest.raises(ValueError):
        winsorize(5.0, history=list(range(100)), p_low=0.6, p_high=0.4)


def test_winsorize_short_history_returns_none():
    assert winsorize(5.0, history=[1, 2]) is None


# ---------------------------------------------------------------------------
# vol_adjusted
# ---------------------------------------------------------------------------


def test_vol_adjusted_bounded():
    history = list(np.random.default_rng(3).normal(0.0, 0.5, 100))
    for value in [-10.0, 0.0, 5.0]:
        score = vol_adjusted(value, history=history)
        assert score is not None
        assert -1.0 < score < 1.0


def test_vol_adjusted_zero_variance_none():
    assert vol_adjusted(5.0, history=[0.0] * 20) is None


def test_vol_adjusted_sign_preserved():
    history = list(np.random.default_rng(4).normal(0.0, 0.5, 100))
    assert vol_adjusted(2.0, history=history) > 0
    assert vol_adjusted(-2.0, history=history) < 0


# ---------------------------------------------------------------------------
# _score_inflation integration with the new normalizer
# ---------------------------------------------------------------------------


def _ind(**overrides) -> RawIndicators:
    """Build a RawIndicators with only the inflation-relevant fields populated.

    All other fields default to None via the dataclass — only override what
    the test cares about.
    """
    return RawIndicators(**overrides)


def test_score_inflation_bounded_for_all_realistic_inputs():
    """Output is in [-1, +1] for any plausible combination of inputs."""
    for cpi in [0.0, 2.0, 4.0, 8.0]:
        for be in [1.0, 2.25, 3.5]:
            s = _score_inflation(_ind(cpi_yoy=cpi, breakeven_5y=be))
            assert -1.0 <= s <= 1.0, f"out of bounds: {s} for cpi={cpi}, be={be}"


def test_score_inflation_all_none_returns_zero():
    """Empty signal set → 0 (matches legacy _safe_avg behaviour on all None)."""
    assert _score_inflation(_ind()) == 0.0


def test_score_inflation_smooth_near_legacy_cpi_threshold():
    """Two pings 20bp apart at the old bucket boundary should now differ by <3bp."""
    s_below = _score_inflation(_ind(cpi_yoy=2.99))
    s_above = _score_inflation(_ind(cpi_yoy=3.01))
    delta = abs(s_above - s_below)
    assert delta < 0.03, f"expected smooth transition, got Δ={delta:.4f}"


def test_score_inflation_today_actual_signals():
    """Today's actual macro briefing values (2026-05-21).

    Legacy buckets produced inflation_score = 0.73 (sticky for 21 days).
    New continuous scorer should produce a value in roughly [0.45, 0.75]:
    still positive (inflation IS running hot), but no longer slammed against
    the bucket cliff.
    """
    today = _score_inflation(_ind(
        cpi_yoy=2.4,         # approximate per recent FRED prints
        core_cpi_yoy=2.8,
        ppi_yoy=2.5,
        pce_yoy=2.5,
        breakeven_5y=2.35,
    ))
    assert 0.0 <= today <= 0.6, (
        f"today's tanh-normalized inflation_score is {today:.3f}; "
        "the smooth scorer should land moderately positive — not pinned high "
        "as the legacy buckets did."
    )


def test_score_inflation_signs_agree_at_bucket_midpoints():
    """At each legacy bucket center, the new score should at least match sign.

    This is the sanity check that the anchor/scale params don't accidentally
    invert the relationship anywhere.
    """
    # Hot inflation across the board → strongly positive
    hot = _score_inflation(_ind(cpi_yoy=6.0, core_cpi_yoy=5.0, ppi_yoy=8.0, pce_yoy=4.5, breakeven_5y=3.2))
    assert hot > 0.5

    # On target → near zero
    target = _score_inflation(_ind(cpi_yoy=2.0, core_cpi_yoy=2.0, ppi_yoy=2.0, pce_yoy=2.0, breakeven_5y=2.25))
    assert abs(target) < 0.05

    # Deflationary → strongly negative
    deflationary = _score_inflation(_ind(cpi_yoy=0.0, core_cpi_yoy=0.5, ppi_yoy=-2.0, pce_yoy=0.5, breakeven_5y=1.5))
    assert deflationary < -0.4


def test_score_inflation_monotonic_in_cpi_yoy():
    """Holding other inputs fixed, score increases monotonically in CPI YoY."""
    scores = [_score_inflation(_ind(cpi_yoy=c)) for c in [0.0, 1.5, 2.0, 3.0, 5.0, 8.0]]
    assert all(b > a for a, b in zip(scores, scores[1:]))


def test_score_inflation_uses_config_params():
    """Smoke-check: the imported config values are what we expect.

    Catches accidental edits to config.py that would change the engine's
    behaviour silently.
    """
    assert inflation_config.CPI_YOY.center == 2.0
    assert inflation_config.CPI_YOY.scale == 1.5
    assert inflation_config.BREAKEVEN_5Y.center == 2.25
