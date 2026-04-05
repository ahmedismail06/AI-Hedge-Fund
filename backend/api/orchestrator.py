"""
Orchestrator API — FastAPI router.

Endpoints:
  GET  /orchestrator/status      — mode, suspended_today, critical_alert_count, last_cycle_ts
  POST /orchestrator/cycle/run   — manually trigger one approval-pass cycle
  GET  /orchestrator/mode        — {mode, suspended_until}
  POST /orchestrator/mode        — toggle SUPERVISED / AUTONOMOUS
  GET  /orchestrator/log         — audit log (default: today, limit 100)
"""

from dotenv import load_dotenv

load_dotenv()

import logging
from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.agents.orchestrator import (
    _get_config,
    _has_critical_alerts,
    _log_event,
    _set_mode,
    _set_suspended_until,
    run_orchestrator_cycle,
)
from backend.memory.vector_store import _get_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/orchestrator", tags=["orchestrator"])


# ── GET /orchestrator/status ──────────────────────────────────────────────────


@router.get("/status")
def get_orchestrator_status():
    """
    Returns current mode, suspension state, unresolved CRITICAL alert count,
    and the timestamp of the last logged cycle event.
    """
    try:
        config = _get_config()
        mode = config.get("mode", "SUPERVISED")
        suspended_until = config.get("suspended_until")

        suspended_today = False
        if suspended_until:
            try:
                suspended_today = date.fromisoformat(str(suspended_until)[:10]) == date.today()
            except (ValueError, TypeError):
                pass

        # Count unresolved CRITICAL alerts
        critical_count = 0
        try:
            resp = (
                _get_client()
                .table("risk_alerts")
                .select("id", count="exact")
                .eq("severity", "CRITICAL")
                .eq("resolved", False)
                .execute()
            )
            critical_count = resp.count or 0
        except Exception as exc:
            logger.warning("get_orchestrator_status: CRITICAL count failed — %s", exc)

        # Last cycle timestamp
        last_cycle_ts = None
        try:
            log_resp = (
                _get_client()
                .table("orchestrator_log")
                .select("created_at")
                .eq("event_type", "CYCLE_END")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if log_resp.data:
                last_cycle_ts = log_resp.data[0]["created_at"]
        except Exception as exc:
            logger.warning("get_orchestrator_status: last cycle ts failed — %s", exc)

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Supabase error: {exc}")

    return {
        "mode": mode,
        "suspended_today": suspended_today,
        "suspended_until": suspended_until,
        "critical_alert_count": critical_count,
        "last_cycle_ts": last_cycle_ts,
    }


# ── POST /orchestrator/cycle/run ──────────────────────────────────────────────


@router.post("/cycle/run")
async def trigger_cycle(portfolio_value: float = Query(25000.0, gt=0)):
    """
    Manually trigger one orchestrator approval-pass cycle.
    Useful for testing or recovering from a missed schedule window.
    """
    try:
        result = await run_orchestrator_cycle(portfolio_value=portfolio_value)
    except Exception as exc:
        logger.exception("POST /orchestrator/cycle/run failed")
        raise HTTPException(status_code=500, detail=f"Orchestrator cycle error: {exc}")
    return result


# ── GET /orchestrator/mode ────────────────────────────────────────────────────


@router.get("/mode")
def get_mode():
    """Return current mode (SUPERVISED | AUTONOMOUS) and suspension state."""
    try:
        config = _get_config()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Supabase error: {exc}")

    return {
        "mode": config.get("mode", "SUPERVISED"),
        "suspended_until": config.get("suspended_until"),
    }


# ── POST /orchestrator/mode ───────────────────────────────────────────────────


class ModeRequest(BaseModel):
    mode: Literal["SUPERVISED", "AUTONOMOUS"]


@router.post("/mode")
def set_mode(body: ModeRequest):
    """
    Toggle between SUPERVISED and AUTONOMOUS mode.
    Switching to SUPERVISED also clears suspended_until (human takes control).
    """
    try:
        current_config = _get_config()
        old_mode = current_config.get("mode", "SUPERVISED")

        updated = _set_mode(body.mode)

        # Clear suspension when switching to SUPERVISED
        if body.mode == "SUPERVISED":
            _set_suspended_until(None)

        _log_event(
            "MODE_CHANGE",
            agent="orchestrator",
            detail=f"Mode changed: {old_mode} → {body.mode}",
            mode_snapshot=body.mode,
        )
        logger.info("Orchestrator mode changed: %s → %s", old_mode, body.mode)

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Supabase error: {exc}")

    return {
        "mode": updated.get("mode", body.mode),
        "previous_mode": old_mode,
        "suspended_until": updated.get("suspended_until"),
    }


# ── GET /orchestrator/log ─────────────────────────────────────────────────────


@router.get("/log")
def get_log(
    run_date: str | None = Query(None, description="YYYY-MM-DD (defaults to today)"),
    limit: int = Query(100, ge=1, le=500),
):
    """
    Return orchestrator_log rows for a given run_date (default: today).
    Newest first.
    """
    target_date = run_date or date.today().isoformat()

    try:
        resp = (
            _get_client()
            .table("orchestrator_log")
            .select("*")
            .eq("run_date", target_date)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Supabase error: {exc}")

    return resp.data or []
