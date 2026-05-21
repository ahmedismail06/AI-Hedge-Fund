"""Inflation scoring engine — layered factor architecture.

Public API (built up incrementally across PRs 1-5):

PR 1 (current):
    - backend.macro.inflation_engine.normalize: continuous normalization toolkit
    - backend.macro.inflation_engine.config:    per-indicator anchor/scale parameters

PR 2: IndicatorMeta + age-decay confidence
PR 3: Layer modules (structural, momentum, market_expectations, surprise)
PR 4: ConsensusSource Protocol + ManualConsensusSource + dynamic IC weighting
PR 5: Tests + diagnostics polish

Design reference: docs/inflation_engine_design.md
"""

from backend.macro.inflation_engine.normalize import (
    tanh_norm,
    rolling_zscore,
    percentile_rank,
    winsorize,
    vol_adjusted,
)

__all__ = [
    "tanh_norm",
    "rolling_zscore",
    "percentile_rank",
    "winsorize",
    "vol_adjusted",
]
