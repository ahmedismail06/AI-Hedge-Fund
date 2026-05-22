"""Diagnostics and persistence for the inflation engine.

PR 4 of the inflation engine redesign (docs/inflation_engine_design.md §4).

Handles persisting InflationScoreResult to the ``inflation_diagnostics``
Supabase table and retrieving historical performance (IC) for dynamic
weighting.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, Optional

from backend.db.utils import get_supabase_client
from backend.macro.inflation_engine.types import InflationScoreResult

logger = logging.getLogger(__name__)

_LAYER_NAMES = ["structural", "momentum", "market_expectations", "surprise"]
_IC_WINDOW = 90


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

    Cold-start behaviour:
      - Fewer than 90 usable rows → return None (caller falls back to static).
      - Missing snapshot CPI history → return None.

    When enough history is available, returns per-layer ICs normalised to
    [0, 1] using ``(spearman_rho + 1) / 2``.
    """
    try:
        supabase = get_supabase_client()
        response = (
            supabase.table("inflation_diagnostics")
            .select("date, layer_scores, result_json")
            .order("date", desc=True)
            .limit(_IC_WINDOW)
            .execute()
        )
    except Exception as exc:
        logger.warning("get_latest_ics: failed to read diagnostics history: %s", exc)
        return None

    rows = list(reversed(response.data or []))
    return _calculate_layer_ics(rows)


def _calculate_layer_ics(rows: list[dict[str, Any]]) -> Optional[Dict[str, float]]:
    """Calculate normalised per-layer ICs from persisted diagnostics rows."""
    records: list[dict[str, Any]] = []
    for row in rows:
        result_json = row.get("result_json") or {}
        diagnostics = result_json.get("diagnostics") or {}
        snapshot = diagnostics.get("snapshot") or {}
        cpi_yoy = snapshot.get("cpi_yoy")
        layer_scores = row.get("layer_scores") or diagnostics.get("layer_scores") or {}
        if cpi_yoy is None or not layer_scores:
            continue
        records.append(
            {
                "date": row.get("date"),
                "cpi_yoy": float(cpi_yoy),
                "layer_scores": layer_scores,
            }
        )

    if len(records) < _IC_WINDOW:
        return None

    latest_cpi_yoy = records[-1]["cpi_yoy"]
    outcome_vector = [latest_cpi_yoy - record["cpi_yoy"] for record in records]

    if len(set(outcome_vector)) <= 1:
        return None

    ics: dict[str, float] = {}
    for layer_name in _LAYER_NAMES:
        x_values: list[float] = []
        y_values: list[float] = []
        for record, realized_delta in zip(records, outcome_vector):
            layer_score = record["layer_scores"].get(layer_name)
            if layer_score is None:
                continue
            x_values.append(float(layer_score))
            y_values.append(float(realized_delta))

        rho = _spearman_rank_correlation(x_values, y_values)
        if rho is None:
            return None
        ics[layer_name] = round(max(0.0, min(1.0, (rho + 1.0) / 2.0)), 4)

    return ics


def _spearman_rank_correlation(x_values: list[float], y_values: list[float]) -> Optional[float]:
    """Compute Spearman rank correlation without adding a scipy dependency."""
    if len(x_values) != len(y_values) or len(x_values) < _IC_WINDOW:
        return None
    if len(set(x_values)) <= 1 or len(set(y_values)) <= 1:
        return None

    x_ranks = _average_ranks(x_values)
    y_ranks = _average_ranks(y_values)

    x_mean = sum(x_ranks) / len(x_ranks)
    y_mean = sum(y_ranks) / len(y_ranks)

    numerator = sum((xr - x_mean) * (yr - y_mean) for xr, yr in zip(x_ranks, y_ranks))
    x_var = sum((xr - x_mean) ** 2 for xr in x_ranks)
    y_var = sum((yr - y_mean) ** 2 for yr in y_ranks)
    if x_var == 0.0 or y_var == 0.0:
        return None

    return numerator / ((x_var * y_var) ** 0.5)


def _average_ranks(values: list[float]) -> list[float]:
    """Assign average ranks for ties."""
    ordered = sorted((value, idx) for idx, value in enumerate(values))
    ranks = [0.0] * len(values)
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1][0] == ordered[i][0]:
            j += 1
        avg_rank = (i + j + 2) / 2.0
        for k in range(i, j + 1):
            _, original_idx = ordered[k]
            ranks[original_idx] = avg_rank
        i = j + 1
    return ranks


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
