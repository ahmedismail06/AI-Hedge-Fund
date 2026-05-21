"""Diagnostics and persistence for the inflation engine.

PR 4 of the inflation engine redesign (docs/inflation_engine_design.md §4).

Handles persisting InflationScoreResult to the ``inflation_diagnostics``
Supabase table and retrieving historical performance (IC) for dynamic
weighting.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Dict, Optional

from backend.db.utils import get_supabase_client
from backend.macro.inflation_engine.types import InflationScoreResult

logger = logging.getLogger(__name__)


def persist_result(run_date: date, result: InflationScoreResult) -> bool:
    """Persist an inflation scoring result to Supabase.

    Parameters
    ----------
    run_date:
        The date of the scoring run.
    result:
        The full result object from the aggregator.

    Returns
    -------
    bool:
        True if successful, False otherwise.
    """
    try:
        supabase = get_supabase_client()
        
        # Prepare the row for inflation_diagnostics.
        payload = {
            "date": run_date.isoformat(),
            "score": result.score,
            "confidence": result.confidence,
            "aggregate_method": result.aggregate_method,
            # Flatten diagnostics for easier SQL querying.
            "layer_scores": result.diagnostics.get("layer_scores"),
            "layer_confidences": result.diagnostics.get("layer_confidences"),
            "normalised_weights": result.diagnostics.get("normalised_weights"),
            "layer_ics": result.diagnostics.get("layer_ics"),
            # Store the full result as JSON for deep audit.
            "result_json": _to_json_serializable(result),
        }

        supabase.table("inflation_diagnostics").upsert(payload).execute()
        logger.info("persist_result: saved inflation diagnostics for %s", run_date)
        return True

    except Exception as e:
        logger.error("persist_result: failed to save to Supabase: %s", e)
        return False


def get_latest_ics() -> Optional[Dict[str, float]]:
    """Retrieve the most recent ICs for dynamic weighting.

    In PR4, this is a stub that returns None (triggering 1.0 defaults).
    In a full implementation, it would query historical performance.
    """
    # TODO: Implement IC calculation/retrieval from historical scores vs realized CPI.
    return None


def _to_json_serializable(result: InflationScoreResult) -> dict:
    """Convert InflationScoreResult to a JSON-serializable dict."""
    # This is a helper to ensure complex types (like datetime) are handled if any.
    # For now, most fields in result are already serializable or can be coerced.
    return {
        "score": result.score,
        "confidence": result.confidence,
        "aggregate_method": result.aggregate_method,
        "stale_warnings": result.stale_warnings,
        "diagnostics": result.diagnostics,
        "layers": {
            name: {
                "score": layer.score,
                "confidence": layer.confidence,
                "notes": layer.notes,
                "contributors": [
                    {
                        "indicator": c.indicator,
                        "raw_value": c.raw_value,
                        "signal": c.signal,
                        "weight": c.weight,
                        "confidence": c.confidence,
                    }
                    for c in layer.contributors
                ],
            }
            for name, layer in result.layers.items()
        },
    }
