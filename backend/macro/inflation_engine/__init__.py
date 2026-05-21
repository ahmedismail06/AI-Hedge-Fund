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

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

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
    ConsensusExpectation,
    ConsensusSource,
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
from backend.macro.inflation_engine.layers import (
    structural as structural_layer,
    momentum as momentum_layer,
    market_expectations as market_layer,
    surprise as surprise_layer,
)
from backend.macro.inflation_engine.aggregation import aggregate
from backend.macro.inflation_engine.diagnostics import get_latest_ics, persist_result

logger = logging.getLogger(__name__)

__all__ = [
    # entry point
    "score_inflation",
    # normalize
    "tanh_norm",
    "rolling_zscore",
    "percentile_rank",
    "winsorize",
    "vol_adjusted",
    # types
    "IndicatorMeta",
    "FREQUENCY_DAYS",
    "Contribution",
    "LayerOutput",
    "InflationSnapshot",
    "InflationScoreResult",
    "ConsensusExpectation",
    "ConsensusSource",
    # staleness
    "staleness_confidence",
    "confidence_from_meta",
    "HALFLIVES",
    # config
    "AggregationWeights",
    "DEFAULT_AGGREGATION_WEIGHTS",
]


def score_inflation(
    snapshot: InflationSnapshot,
    weights: Optional[AggregationWeights] = None,
    consensus_source: Optional[ConsensusSource] = None,
    run_date: Optional[date] = None,
    persist: bool = False,
) -> InflationScoreResult:
    """Entry point for the layered inflation scoring engine.

    PR 4 implementation:
      - Fetches consensus data if a source is provided.
      - Retrieves latest ICs for dynamic weighting.
      - Runs all four layers (Structural, Momentum, Market, Surprise).
      - Aggregates and optionally persists to Supabase.

    Parameters
    ----------
    snapshot:
        InflationSnapshot containing raw indicator values and metadata.
    weights:
        Base weights per layer (defaults to 40/25/25/10).
    consensus_source:
        Source for consensus expectations (e.g. ManualConsensusSource).
    run_date:
        The date of the scoring run (defaults to today).
    persist:
        If True, persists the result to the ``inflation_diagnostics`` table.

    Returns
    -------
    InflationScoreResult
        Full result with per-layer decomposition and attribution.
    """
    if run_date is None:
        run_date = date.today()

    # 1. Enrich snapshot with consensus data if available.
    if consensus_source is not None:
        # We look for expectations released in the last 30 days.
        since = snapshot.release_dates.get("cpi", snapshot.release_dates.get("core_cpi"))
        if since is None:
            # Fallback: last 30 days.
            from datetime import datetime, timedelta
            since = datetime.combine(run_date, datetime.min.time()) - timedelta(days=30)
        
        snapshot.expectations = consensus_source.get_expectations(since)

    # 2. Retrieve ICs for dynamic weighting.
    ics = get_latest_ics()

    # 3. Compute layers.
    layers = {
        "structural": structural_layer.compute(snapshot),
        "momentum": momentum_layer.compute(snapshot),
        "market_expectations": market_layer.compute(snapshot),
        "surprise": surprise_layer.compute(snapshot),
    }

    # 4. Aggregate.
    result = aggregate(layers, weights=weights, layer_ics=ics)

    # 5. Persist (optional).
    if persist:
        persist_result(run_date, result)

    return result
