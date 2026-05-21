"""Per-indicator normalization parameters and engine-wide configuration.

These constants encode the economic anchors used by `tanh_norm`. They are
deliberately conservative — the goal is to map "around target" to 0, "two
standard policy deviations above target" to a strongly positive score, and
"actively deflationary" to a strongly negative score, without producing
saturation at every print.

Calibration notes:
  - CPI / Core CPI: center = Fed 2 % target. scale = 1.5 → ±1.5 % from target
    maps to ±tanh(1) ≈ ±0.76. CPI at 5 % YoY → tanh((5 - 2) / 1.5) = tanh(2) ≈ 0.96.
  - PPI: center = 2 %. scale = 3 (PPI is structurally more volatile than CPI;
    a wider scale prevents over-sensitivity to commodity passthrough).
  - PCE: center = 2 % (Fed's preferred gauge — same target). scale = 1.2
    (PCE is smoother than CPI; a tighter scale reflects lower base volatility).
  - 5Y Breakeven: center = 2.25 % (Fed target plus a small term premium that
    has been the empirical anchor since QE). scale = 0.5 (breakevens are
    highly stable; a small deviation is meaningful).

The legacy buckets are listed in comments for each indicator as a reference
for reviewers — the new continuous transform should agree with bucket signs
at the bucket midpoints but produce smooth, monotone outputs everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TanhParams:
    """Anchor + scale for a tanh_norm call.

    Attributes:
        center: Value mapping to 0.0 (the economic anchor).
        scale:  Characteristic deviation — ±1×scale ≈ ±0.76 in tanh space.
    """
    center: float
    scale: float


# ── CPI ──────────────────────────────────────────────────────────────────────
# Legacy buckets: >5 → +1.0 | ≥3 → +0.5 | ≥2 → 0.0 | ≥1 → -0.5 | else → -1.0
# New continuous: tanh_norm(value, center=2.0, scale=1.5)
#   2.0 → 0.00   3.0 → +0.46   4.0 → +0.76   5.0 → +0.91   6.0 → +0.96
#   1.0 → -0.46  0.5 → -0.64  -1.0 → -0.91
CPI_YOY        = TanhParams(center=2.0, scale=1.5)
CORE_CPI_YOY   = TanhParams(center=2.0, scale=1.5)

# ── PPI ──────────────────────────────────────────────────────────────────────
# Legacy buckets: >6 → +1.0 | ≥3 → +0.5 | ≥1 → 0.0 | ≥0 → -0.5 | else → -1.0
# New continuous: tanh_norm(value, center=2.0, scale=3.0)
#   2.0 → 0.00   5.0 → +0.76   8.0 → +0.96
#  -1.0 → -0.76 -4.0 → -0.96
# PPI is structurally more volatile than CPI; wider scale prevents oscillation.
PPI_YOY        = TanhParams(center=2.0, scale=3.0)

# ── PCE ──────────────────────────────────────────────────────────────────────
# Legacy buckets: >4 → +1.0 | ≥2.5 → +0.5 | ≥2.0 → 0.0 | ≥1.5 → -0.5 | else → -1.0
# New continuous: tanh_norm(value, center=2.0, scale=1.2)
#   2.0 → 0.00   3.2 → +0.76   4.4 → +0.96
#   0.8 → -0.76  1.5 → -0.40
PCE_YOY        = TanhParams(center=2.0, scale=1.2)

# ── 5Y Breakeven ─────────────────────────────────────────────────────────────
# Legacy buckets: >3 → +1.0 | ≥2.5 → +0.5 | ≥2 → 0.0 | ≥1.5 → -0.5 | else → -1.0
# New continuous: tanh_norm(value, center=2.25, scale=0.5)
#   2.25 → 0.00   2.75 → +0.76   3.25 → +0.96
#   1.75 → -0.76  1.25 → -0.96
# Breakevens are highly stable; a small deviation carries meaningful information.
BREAKEVEN_5Y   = TanhParams(center=2.25, scale=0.5)

# ── New in PR3 ───────────────────────────────────────────────────────────────

# ── Sticky-price CPI (Atlanta Fed) ───────────────────────────────────────────
# Sticky-price CPI is structurally less volatile than headline CPI; same center
# (2%) but slightly tighter scale (1.2) to match its historically lower variance.
STICKY_CPI_YOY = TanhParams(center=2.0, scale=1.2)

# ── Trimmed-mean PCE (Dallas Fed) ────────────────────────────────────────────
# 12-month trimmed-mean PCE: closely tracks core PCE; same parameters as PCE.
TRIMMED_MEAN_PCE_YOY = TanhParams(center=2.0, scale=1.2)

# ── 5Y5Y Forward Inflation Rate ──────────────────────────────────────────────
# T5YIFR: the market's view of inflation 5 years out.  Anchored at 2.30%
# (historically trades 5-10bp above the 5Y breakeven).  Tight scale = 0.5
# (same reasoning as BREAKEVEN_5Y — forward rates are highly stable).
FIVE_Y_FIVE_Y_FWD = TanhParams(center=2.30, scale=0.5)

# ── Momentum layer tanh parameters ───────────────────────────────────────────
# For 3m-annualised and regression-slope inputs, the anchor is 0.0 (flat trend),
# and the scale is chosen so that a 1 pp annualised CPI change maps to ≈ 0.46
# (= tanh(1/2.0)).  This gives good sensitivity in the ±1-2 pp range where
# most inflation momentum moves occur, without saturating during large swings.
#
# Calibration example for CPI_MOM_3M (scale=2.0):
#   +2 pp ann → tanh(1.0) ≈ +0.76  (acceleration)
#   +1 pp ann → tanh(0.5) ≈ +0.46  (moderate acceleration)
#    0 pp ann →  0.0                (flat trend)
#   -1 pp ann → tanh(-0.5) ≈ -0.46 (moderate deceleration)
#   -2 pp ann → tanh(-1.0) ≈ -0.76 (deceleration)
MOMENTUM_3M = TanhParams(center=0.0, scale=2.0)

# Regression slope: OLS slope over 6 months (pp per month).
# A slope of ±0.15 pp/month (≈ ±1.8 pp annualised acceleration) maps to ≈ ±0.46.
MOMENTUM_SLOPE_6M = TanhParams(center=0.0, scale=0.3)


# ── Aggregation layer weights ─────────────────────────────────────────────────

@dataclass(frozen=True)
class AggregationWeights:
    """Base weights per layer before confidence scaling.

    Effective weight = base_weight × layer.confidence.  Surprise layer is
    absent (confidence=0) in PR3, so its 10% is redistributed proportionally
    to the remaining three layers.
    """
    structural: float = 0.40
    momentum: float = 0.25
    market_expectations: float = 0.25
    surprise: float = 0.10

    def as_dict(self) -> dict:
        return {
            "structural": self.structural,
            "momentum": self.momentum,
            "market_expectations": self.market_expectations,
            "surprise": self.surprise,
        }


# Default instance used by aggregation.py and scorer.py.
DEFAULT_AGGREGATION_WEIGHTS = AggregationWeights()
