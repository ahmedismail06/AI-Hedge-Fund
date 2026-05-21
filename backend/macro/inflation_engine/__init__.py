"""Inflation scoring engine — layered factor architecture.

Public API (built up incrementally across PRs 1-5):

PR 1 (shipped):
    - backend.macro.inflation_engine.normalize: continuous normalization toolkit
    - backend.macro.inflation_engine.config:    per-indicator anchor/scale parameters

PR 2 (shipped):
    - backend.macro.inflation_engine.types:     IndicatorMeta dataclass
    - backend.macro.inflation_engine.staleness: staleness → confidence decay function
    - FredBlock.last_release_dates field (fred_fetcher.py)
    - _score_inflation() uses confidence-weighted aggregation internally

PR 3 (current):
    - backend.macro.inflation_engine.types:          Contribution, LayerOutput,
                                                     InflationSnapshot, InflationScoreResult
    - backend.macro.inflation_engine.config:         AggregationWeights, new FRED params
    - backend.macro.inflation_engine.ingestion:      fred_inflation, market_inputs, consensus
    - backend.macro.inflation_engine.transforms:     momentum, synthetic
    - backend.macro.inflation_engine.layers:         structural, momentum, market_expectations,
                                                     surprise (stub, confidence=0)
    - backend.macro.inflation_engine.aggregation:    combine layers → InflationScoreResult
    - scorer.py: _score_inflation() now delegates to layered engine;
                 legacy_score_inflation() preserves PR2 behaviour

PR 4: ConsensusSource + ManualConsensusSource + dynamic IC weighting
PR 5: Tests + observability polish

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
    Contribution,
    LayerOutput,
    InflationSnapshot,
    InflationScoreResult,
)
from backend.macro.inflation_engine.staleness import (
    staleness_confidence,
    confidence_from_meta,
    HALFLIVES,
)
from backend.macro.inflation_engine.config import (
    AggregationWeights,
    DEFAULT_AGGREGATION_WEIGHTS,
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
    # types (PR 3)
    "Contribution",
    "LayerOutput",
    "InflationSnapshot",
    "InflationScoreResult",
    # staleness (PR 2)
    "staleness_confidence",
    "confidence_from_meta",
    "HALFLIVES",
    # config (PR 3)
    "AggregationWeights",
    "DEFAULT_AGGREGATION_WEIGHTS",
]
