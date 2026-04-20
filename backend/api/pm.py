"""
FastAPI router for the AI Portfolio Manager Agent (Component 8 v2).

Endpoints:
  GET  /pm/status                   — current PM cycle status + portfolio summary
  GET  /pm/decisions                — paginated decision log
  GET  /pm/decisions/{decision_id}  — single decision with full reasoning
  POST /pm/override/{decision_id}   — human override (block/modify/force-execute)
  POST /pm/override/close/{ticker}  — force immediate position close
  POST /pm/override/halt            — halt all new entries
  POST /pm/override/resume          — resume normal operation
  GET  /pm/calibration              — confidence calibration report
  POST /pm/cycle/run                — manually trigger one PM cycle
  GET  /pm/config                   — current PM configuration
  POST /pm/config                   — update PM configuration
"""

from dotenv import load_dotenv

load_dotenv()

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.memory.vector_store import _get_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pm", tags=["PM Agent"])


# ── Request models ────────────────────────────────────────────────────────────

class OverrideRequest(BaseModel):
    override_type: Literal["BLOCK", "MODIFY", "FORCE_EXECUTE"]
    reason: str
    modified_action_details: Optional[Dict[str, Any]] = None


class ConfigUpdateRequest(BaseModel):
    mode: Optional[Literal["autonomous", "supervised"]] = None
    cycle_interval_seconds: Optional[int] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_pm_config_row() -> Dict[str, Any]:
    try:
        resp = _get_client().table("pm_config").select("*").limit(1).execute()
        if resp.data:
            return resp.data[0]
    except Exception as exc:
        logger.warning("pm API: pm_config read failed — %s", exc)
    return {
        "id": 1,
        "mode": "autonomous",
        "cycle_interval_seconds": 300,
        "daily_loss_halt_triggered": False,
        "halted_until": None,
    }


def _get_portfolio_summary() -> Dict[str, Any]:
    """Quick portfolio summary for the status endpoint.

    Uses dollar_size / live portfolio_value so the fractions stay consistent
    with GET /portfolio/exposure (which also uses the exposure tracker).
    Returns fractions (0–1 scale); the Orchestrator frontend multiplies by 100.
    """
    try:
        from backend.broker.ibkr import get_portfolio_value
        from backend.portfolio.exposure_tracker import get_current_exposure

        resp = (
            _get_client()
            .table("positions")
            .select("ticker,direction,dollar_size")
            .eq("status", "OPEN")
            .execute()
        )
        positions = resp.data or []
        portfolio_value = get_portfolio_value()
        exposure = get_current_exposure(positions, portfolio_value)

        gross = exposure["gross_exposure_pct"] / 100  # tracker returns %, convert back to fraction
        net   = exposure["net_exposure_pct"]   / 100

        return {
            "position_count": len(positions),
            "gross_exposure": round(gross, 4),
            "net_exposure": round(net, 4),
            "cash_pct": round(max(0.0, 1.0 - gross), 4),
        }
    except Exception as exc:
        logger.warning("pm API: portfolio summary failed — %s", exc)
        return {"position_count": 0, "gross_exposure": 0.0, "net_exposure": 0.0, "cash_pct": 1.0}


def _get_last_cycle() -> Optional[Dict[str, Any]]:
    """Fetch the most recent pm_decisions row as a cycle proxy."""
    try:
        resp = (
            _get_client()
            .table("pm_decisions")
            .select("timestamp,execution_status,category,decision")
            .order("timestamp", desc=True)
            .limit(1)
            .execute()
        )
        return resp.data[0] if resp.data else None
    except Exception:
        return None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/status")
def get_pm_status():
    """Current PM cycle status + portfolio summary + config."""
    config = _get_pm_config_row()
    portfolio = _get_portfolio_summary()
    last_cycle = _get_last_cycle()

    # Count today's decisions
    today_count = 0
    try:
        from datetime import date
        resp = (
            _get_client()
            .table("pm_decisions")
            .select("id", count="exact")
            .gte("created_at", date.today().isoformat())
            .execute()
        )
        today_count = resp.count or 0
    except Exception:
        pass

    # Active critical alerts
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
    except Exception:
        pass

    return {
        "mode": config.get("mode", "autonomous"),
        "cycle_interval_seconds": config.get("cycle_interval_seconds", 300),
        "daily_loss_halt_triggered": config.get("daily_loss_halt_triggered", False),
        "halted_until": config.get("halted_until"),
        "portfolio": portfolio,
        "last_cycle": last_cycle,
        "decisions_today": today_count,
        "active_critical_alerts": critical_count,
    }


@router.get("/decisions")
def get_pm_decisions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    category: Optional[str] = Query(default=None),
    ticker: Optional[str] = Query(default=None),
    execution_status: Optional[str] = Query(default=None),
):
    """Paginated PM decision log with optional filters."""
    try:
        query = (
            _get_client()
            .table("pm_decisions")
            .select("*")
            .order("timestamp", desc=True)
            .range(offset, offset + limit - 1)
        )
        if category:
            query = query.eq("category", category)
        if ticker:
            query = query.eq("ticker", ticker.upper())
        if execution_status:
            query = query.eq("execution_status", execution_status)

        resp = query.execute()
        return resp.data or []
    except Exception as exc:
        logger.error("get_pm_decisions: Supabase error — %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/decisions/{decision_id}")
def get_pm_decision(decision_id: str):
    """Single decision with full reasoning chain."""
    try:
        resp = (
            _get_client()
            .table("pm_decisions")
            .select("*")
            .eq("decision_id", decision_id)
            .limit(1)
            .execute()
        )
        if not resp.data:
            raise HTTPException(status_code=404, detail=f"Decision {decision_id} not found")
        return resp.data[0]
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("get_pm_decision: Supabase error — %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/override/{decision_id}")
def override_decision(decision_id: str, body: OverrideRequest):
    """
    Human override for a specific PM decision.
    BLOCK: prevents execution of a SENT_TO_EXECUTION decision.
    MODIFY: updates action_details before execution.
    FORCE_EXECUTE: forces execution even if PM decided to DEFER/REJECT.
    """
    try:
        resp = (
            _get_client()
            .table("pm_decisions")
            .select("*")
            .eq("decision_id", decision_id)
            .limit(1)
            .execute()
        )
        if not resp.data:
            raise HTTPException(status_code=404, detail=f"Decision {decision_id} not found")

        override_record = {
            "override_type": body.override_type,
            "reason": body.reason,
            "original_decision_id": decision_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        update: Dict[str, Any] = {"human_override": override_record}

        if body.override_type == "BLOCK":
            update["execution_status"] = "BLOCKED"
        elif body.override_type == "FORCE_EXECUTE":
            update["execution_status"] = "SENT_TO_EXECUTION"
        elif body.override_type == "MODIFY" and body.modified_action_details:
            update["action_details"] = body.modified_action_details
            update["execution_status"] = "SENT_TO_EXECUTION"

        _get_client().table("pm_decisions").update(update).eq(
            "decision_id", decision_id
        ).execute()

        # For FORCE_EXECUTE on NEW_ENTRY decisions, also flip the positions row so
        # the execution agent picks it up (supervised-mode human approval path).
        decision = resp.data[0]
        if body.override_type in ("FORCE_EXECUTE", "MODIFY") and decision.get("category") == "NEW_ENTRY":
            d_ticker = decision.get("ticker")
            if d_ticker:
                try:
                    pos_resp = (
                        _get_client()
                        .table("positions")
                        .select("id")
                        .eq("ticker", d_ticker)
                        .eq("status", "PENDING_APPROVAL")
                        .execute()
                    )
                    if pos_resp.data:
                        _get_client().table("positions").update({"status": "APPROVED"}).eq(
                            "id", pos_resp.data[0]["id"]
                        ).execute()
                        logger.info(
                            "PM override FORCE_EXECUTE: position for %s set to APPROVED", d_ticker
                        )
                except Exception as pos_exc:
                    logger.warning(
                        "PM override: failed to approve position for %s — %s", d_ticker, pos_exc
                    )

        logger.info(
            "PM override: %s on decision %s — reason: %s",
            body.override_type,
            decision_id,
            body.reason,
        )
        return {"decision_id": decision_id, "override_applied": body.override_type}

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("override_decision: failed — %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/override/close/{ticker}")
def force_close_position(ticker: str, reason: str = "Human override — forced close"):
    """Force immediate close of a position regardless of PM decision state."""
    ticker = ticker.upper()
    try:
        # Find the OPEN position
        resp = (
            _get_client()
            .table("positions")
            .select("id,ticker,share_count,current_price")
            .eq("ticker", ticker)
            .eq("status", "OPEN")
            .limit(1)
            .execute()
        )
        if not resp.data:
            raise HTTPException(
                status_code=404, detail=f"No OPEN position found for {ticker}"
            )

        position = resp.data[0]

        # Mark for execution as a CLOSE
        _get_client().table("positions").update({
            "exit_action": "CLOSE",
            "status": "APPROVED",
        }).eq("id", position["id"]).execute()

        # Log this as a PM decision override
        from backend.agents.orchestrator import (
            _next_decision_id,
            _build_decision_record,
            _log_pm_decision,
            _get_pm_config,
        )
        from backend.agents.pm_prompts.base_context import build_base_context

        base_ctx = build_base_context(_get_client())
        from backend.agents.orchestrator import _snapshot

        decision_id = _next_decision_id()
        record = _build_decision_record(
            decision_id=decision_id,
            category="EXIT_TRIM",
            ticker=ticker,
            decision="CLOSE",
            action_details={"close_reason": reason, "human_initiated": True},
            reasoning=f"Human-initiated forced close: {reason}",
            risk_assessment="Human override — standard risk assessment bypassed.",
            confidence=1.0,
            context_snapshot=_snapshot(base_ctx),
            hard_blocks_checked={},
            execution_status="SENT_TO_EXECUTION",
        )
        record["human_override"] = {
            "override_type": "FORCE_EXECUTE",
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _log_pm_decision(record)

        logger.info("PM: human forced close for %s — %s", ticker, reason)
        return {"ticker": ticker, "action": "CLOSE", "decision_id": decision_id}

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("force_close_position: failed — %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/override/halt")
def halt_pm(reason: str = "Human override — halt new entries"):
    """Halt all new position entries. PM continues to monitor and evaluate exits."""
    try:
        _get_client().table("pm_config").update({
            "daily_loss_halt_triggered": True,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", 1).execute()

        logger.warning("PM: halted by human — %s", reason)
        return {"status": "halted", "reason": reason}
    except Exception as exc:
        logger.error("halt_pm: failed — %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/override/resume")
def resume_pm(reason: str = "Human override — resume normal operation"):
    """Resume normal PM operation after a halt."""
    try:
        _get_client().table("pm_config").update({
            "daily_loss_halt_triggered": False,
            "halted_until": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", 1).execute()

        logger.info("PM: resumed by human — %s", reason)
        return {"status": "resumed", "reason": reason}
    except Exception as exc:
        logger.error("resume_pm: failed — %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/calibration")
def get_calibration():
    """
    Confidence calibration report: PM confidence scores vs actual outcomes.
    Joins pm_decisions with pm_calibration where outcomes are available.
    """
    try:
        resp = (
            _get_client()
            .table("pm_calibration")
            .select(
                "decision_id,confidence_at_entry,confidence_at_exit,"
                "holding_period_days,return_pct,was_correct,created_at"
            )
            .order("created_at", desc=True)
            .limit(200)
            .execute()
        )
        rows = resp.data or []

        if not rows:
            return {"message": "No calibration data yet — tracks after positions close", "rows": []}

        # Summary stats
        correct = [r for r in rows if r.get("was_correct") is True]
        high_confidence = [r for r in rows if (r.get("confidence_at_entry") or 0) >= 0.7]
        high_conf_correct = [r for r in high_confidence if r.get("was_correct") is True]

        summary = {
            "total_decisions": len(rows),
            "correct_pct": round(len(correct) / len(rows), 4) if rows else None,
            "high_confidence_correct_pct": (
                round(len(high_conf_correct) / len(high_confidence), 4)
                if high_confidence else None
            ),
            "avg_return_pct": (
                round(
                    sum(r.get("return_pct") or 0 for r in rows) / len(rows), 4
                ) if rows else None
            ),
            "avg_holding_days": (
                round(
                    sum(r.get("holding_period_days") or 0 for r in rows) / len(rows), 1
                ) if rows else None
            ),
        }

        return {"summary": summary, "rows": rows}

    except Exception as exc:
        logger.error("get_calibration: failed — %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/cycle/run")
def trigger_pm_cycle(
    portfolio_value: Optional[float] = Query(default=None, ge=0),
):
    """Manually trigger one PM decision cycle. portfolio_value resolved from IBKR if not provided."""
    from backend.agents.orchestrator import run_pm_cycle
    if portfolio_value is None or portfolio_value <= 0:
        from backend.broker.ibkr import get_portfolio_value as _get_pv
        portfolio_value = _get_pv()
    try:
        result = run_pm_cycle(cycle_type="HUMAN_OVERRIDE", portfolio_value=portfolio_value)
        return result
    except Exception as exc:
        logger.error("trigger_pm_cycle: failed — %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/config")
def get_pm_config():
    """Return current PM configuration."""
    return _get_pm_config_row()


@router.post("/config")
def update_pm_config(body: ConfigUpdateRequest):
    """Update PM configuration (mode or cycle interval)."""
    update: Dict[str, Any] = {"updated_at": datetime.now(timezone.utc).isoformat()}

    if body.mode is not None:
        update["mode"] = body.mode
    if body.cycle_interval_seconds is not None:
        if body.cycle_interval_seconds < 60:
            raise HTTPException(
                status_code=400,
                detail="cycle_interval_seconds must be >= 60",
            )
        update["cycle_interval_seconds"] = body.cycle_interval_seconds

    if len(update) == 1:
        raise HTTPException(status_code=400, detail="No valid fields provided to update")

    try:
        resp = (
            _get_client()
            .table("pm_config")
            .update(update)
            .eq("id", 1)
            .execute()
        )
        return resp.data[0] if resp.data else _get_pm_config_row()
    except Exception as exc:
        logger.error("update_pm_config: failed — %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
