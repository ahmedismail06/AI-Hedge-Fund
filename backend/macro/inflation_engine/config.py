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
