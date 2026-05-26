"""
Macro Intelligence Engine API — FastAPI router.

Endpoints:
  GET  /macro/briefing    — today's latest MacroBriefing (full JSON)
  GET  /macro/regime      — lightweight regime + confidence (polled by downstream agents)
  GET  /macro/history     — historical briefings, scalar columns only (default last 30)
  GET  /macro/indicators  — indicator_scores list from the most recent briefing
  POST /macro/run         — manual trigger for testing / ad-hoc re-runs
"""

from dotenv import load_dotenv

load_dotenv()

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query

from backend.memory.vector_store import _get_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/macro", tags=["macro"])


# ── GET /macro/briefing ───────────────────────────────────────────────────────


@router.get("/briefing")
async def get_macro_briefing():
    """Return the most recent daily MacroBriefing as a full JSON object."""
    def _run():
        try:
            client = _get_client()
            result = (
                client.table("macro_briefings")
                .select("briefing_json")
                .order("date", desc=True)
                .limit(1)
                .execute()
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Supabase error: {exc}")

        if not result.data:
            raise HTTPException(status_code=404, detail="No macro briefing found")

        return result.data[0]["briefing_json"]

    return await asyncio.to_thread(_run)


# ── GET /macro/regime ─────────────────────────────────────────────────────────


@router.get("/regime")
async def get_macro_regime():
    """Return current regime + confidence. Lightweight — polled by downstream agents."""
    def _run():
        try:
            client = _get_client()
            result = (
                client.table("macro_briefings")
                .select("date, regime, regime_confidence, regime_changed, regime_score")
                .order("date", desc=True)
                .limit(1)
                .execute()
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Supabase error: {exc}")

        if not result.data:
            raise HTTPException(status_code=404, detail="No macro briefing found")

        return result.data[0]

    return await asyncio.to_thread(_run)


# ── GET /macro/history ────────────────────────────────────────────────────────


@router.get("/history")
async def get_macro_history(limit: int = Query(30, ge=1, le=90)):
    """Return historical macro briefings (scalar columns only, no large JSONB fields)."""
    def _run():
        try:
            client = _get_client()
            result = (
                client.table("macro_briefings")
                .select(
                    "date, regime, regime_score, growth_score, inflation_score, "
                    "fed_score, stress_score, regime_confidence, override_flag, regime_changed"
                )
                .order("date", desc=True)
                .limit(limit)
                .execute()
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Supabase error: {exc}")
        return result.data or []

    return await asyncio.to_thread(_run)


# Maps human-readable indicator names (stored in indicator_scores) to the
# snake_case keys the frontend INDICATOR_LABELS dict expects.
_INDICATOR_NAME_TO_KEY: dict[str, str] = {
    "GDP YoY":            "gdp_growth",
    "ISM Svc PMI":        "ism_pmi",
    "Jobless Claims":     "jobless_claims",
    "Payrolls MoM":       "nonfarm_payrolls",
    "CPI YoY":            "cpi_yoy",
    "Core CPI YoY":       "core_cpi_yoy",
    "PPI YoY":            "ppi_yoy",
    "PCE YoY":            "pce_yoy",
    "5Y Breakeven":       "breakeven_5y",
    "VIX":                "vix",
    "HY Spread":          "hy_spread",
    "DXY":                "dxy",
    "Yield Curve Spread": "yield_curve",
    "SPX 200DMA":         "spx_200dma_pct",
    "3M Treasury":        "treasury_3m",
    "6M Treasury":        "treasury_6m",
    "1Y Treasury":        "treasury_1y",
    "2Y Treasury":        "treasury_2y",
    "5Y Treasury":        "treasury_5y",
    "10Y Treasury":       "treasury_10y",
    "30Y Treasury":       "treasury_30y",
}


# ── GET /macro/indicators ─────────────────────────────────────────────────────


@router.get("/indicators")
async def get_macro_indicators():
    """Return macro indicators as a flat dict keyed by snake_case indicator names."""
    def _run():
        try:
            client = _get_client()
            result = (
                client.table("macro_briefings")
                .select("date, indicator_scores")
                .order("date", desc=True)
                .limit(1)
                .execute()
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Supabase error: {exc}")

        if not result.data:
            raise HTTPException(status_code=404, detail="No macro briefing found")

        row = result.data[0]
        scores: list = row["indicator_scores"] or []

        flat: dict = {"date": row["date"]}
        for entry in scores:
            name = entry.get("name", "")
            key = _INDICATOR_NAME_TO_KEY.get(name)
            if key and entry.get("value") is not None:
                flat[key] = entry["value"]

        return flat

    return await asyncio.to_thread(_run)


# ── GET /macro/inflation/diagnostics ─────────────────────────────────────────


@router.get("/inflation/diagnostics")
async def get_inflation_diagnostics(limit: int = Query(10, ge=1, le=30)):
    """Return the recent layered inflation engine diagnostics."""
    def _run():
        try:
            client = _get_client()
            result = (
                client.table("inflation_diagnostics")
                .select("*")
                .order("date", desc=True)
                .limit(limit)
                .execute()
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Supabase error: {exc}")
        return result.data or []

    return await asyncio.to_thread(_run)


# ── POST /macro/run ───────────────────────────────────────────────────────────


@router.post("/run")
async def trigger_macro_pipeline():
    """
    Manually trigger the macro pipeline. Useful for testing or ad-hoc re-runs
    outside the scheduled 7AM ET window.
    """
    from backend.agents.macro_agent import run_macro_pipeline, MacroAgentError

    try:
        briefing = await asyncio.to_thread(run_macro_pipeline)
    except MacroAgentError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Macro pipeline error: {exc}")

    return briefing.model_dump()
