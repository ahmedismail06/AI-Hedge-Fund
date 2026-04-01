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

import logging

from fastapi import APIRouter, HTTPException, Query

from backend.memory.vector_store import _get_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/macro", tags=["macro"])


# ── GET /macro/briefing ───────────────────────────────────────────────────────


@router.get("/briefing")
def get_macro_briefing():
    """Return the most recent daily MacroBriefing as a full JSON object."""
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


# ── GET /macro/regime ─────────────────────────────────────────────────────────


@router.get("/regime")
def get_macro_regime():
    """Return current regime + confidence. Lightweight — polled by downstream agents."""
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


# ── GET /macro/history ────────────────────────────────────────────────────────


@router.get("/history")
def get_macro_history(limit: int = Query(30, ge=1, le=90)):
    """Return historical macro briefings (scalar columns only, no large JSONB fields)."""
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


# ── GET /macro/indicators ─────────────────────────────────────────────────────


@router.get("/indicators")
def get_macro_indicators():
    """Return the indicator_scores list from the most recent briefing."""
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
    return {"date": row["date"], "indicators": row["indicator_scores"]}


# ── POST /macro/run ───────────────────────────────────────────────────────────


@router.post("/run")
def trigger_macro_pipeline():
    """
    Manually trigger the macro pipeline. Useful for testing or ad-hoc re-runs
    outside the scheduled 7AM ET window.
    """
    from backend.agents.macro_agent import run_macro_pipeline, MacroAgentError

    try:
        briefing = run_macro_pipeline()
    except MacroAgentError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Macro pipeline error: {exc}")

    return briefing.model_dump()
