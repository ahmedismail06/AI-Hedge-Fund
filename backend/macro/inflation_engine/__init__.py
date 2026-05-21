"""Inflation scoring engine — layered factor architecture.

Public API (built up incrementally across PRs 1-5):

PR 1 (shipped):
    - backend.macro.inflation_engine.normalize: continuous normalization toolkit
    - backend.macro.inflation_engine.config:    per-indicator anchor/scale parameters

PR 2 (current):
    - backend.macro.inflation_engine.types:     IndicatorMeta dataclass
    - backend.macro.inflation_engine.staleness: staleness → confidence decay function
    - FredBlock.last_release_dates field (fred_fetcher.py)
    - _score_inflation() now uses confidence-weighted aggregation internally

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
from backend.macro.inflation_engine.types import (
    IndicatorMeta,
    FREQUENCY_DAYS,
)
from backend.macro.inflation_engine.staleness import (
    staleness_confidence,
    confidence_from_meta,
    HALFLIVES,
)

__all__ = [
    # normalize
    "tanh_norm",
    "rolling_zscore",
    "percentile_rank",
    "winsorize",
    "vol_adjusted",
    # types (PR 2)
    "IndicatorMeta",
    "FREQUENCY_DAYS",
    # staleness (PR 2)
    "staleness_confidence",
    "confidence_from_meta",
    "HALFLIVES",
]
