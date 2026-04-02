"""
Risk Agent API — FastAPI router.

Endpoints:
  GET  /risk/alerts                    — recent alerts (unresolved first, last 50)
  GET  /risk/alerts/critical           — CRITICAL-severity alerts only (orchestrator gate)
  POST /risk/alerts/{alert_id}/resolve — mark an alert resolved
  GET  /risk/metrics                   — latest PortfolioMetrics row
  POST /risk/metrics/run               — manually trigger nightly metrics computation
  GET  /risk/status                    — RiskStatus summary (alert counts, suspension flag)
  GET  /risk/stops                     — current stop levels for all OPEN positions
  POST /risk/monitor/run               — manually trigger one monitor cycle
"""

from dotenv import load_dotenv

load_dotenv()

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from backend.agents.risk_agent import RiskAgentError, _get_supabase, _read_macro_regime, run_nightly_metrics, run_risk_monitor
from backend.memory.vector_store import _get_client
from backend.risk.monitor import run_monitor_cycle
from backend.risk.schemas import RiskStatus
from backend.risk.stop_loss import _tier1_threshold

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/risk", tags=["risk"])


# ── GET /risk/alerts ──────────────────────────────────────────────────────────

@router.get("/alerts")
async def get_alerts(limit: int = 50):
    """Return recent risk alerts, unresolved first, then by recency."""
    supabase = _get_client()
    resp = (
        supabase
        .table("risk_alerts")
        .select("*")
        .order("resolved", desc=False)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return resp.data or []


# ── GET /risk/alerts/critical ─────────────────────────────────────────────────

@router.get("/alerts/critical")
async def get_critical_alerts():
    """
    Return unresolved CRITICAL alerts only.
    Used by the orchestrator to gate new trade approvals.
    """
    supabase = _get_client()
    resp = (
        supabase
        .table("risk_alerts")
        .select("*")
        .eq("severity", "CRITICAL")
        .eq("resolved", False)
        .order("created_at", desc=True)
        .execute()
    )
    return resp.data or []


# ── POST /risk/alerts/{alert_id}/resolve ──────────────────────────────────────

@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert(alert_id: str):
    """Mark an alert as resolved and record the resolution timestamp."""
    supabase = _get_client()
    now_iso = datetime.now(timezone.utc).isoformat()

    resp = (
        supabase
        .table("risk_alerts")
        .update({"resolved": True, "resolved_at": now_iso})
        .eq("id", alert_id)
        .execute()
    )

    if not resp.data:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")

    return {"alert_id": alert_id, "resolved": True, "resolved_at": now_iso}


# ── GET /risk/metrics ─────────────────────────────────────────────────────────

@router.get("/metrics")
async def get_metrics():
    """Return the most recently computed PortfolioMetrics row."""
    supabase = _get_client()
    resp = (
        supabase
        .table("portfolio_metrics")
        .select("*")
        .order("date", desc=True)
        .limit(1)
        .execute()
    )
    data = resp.data or []
    if not data:
        return {"detail": "No metrics computed yet."}
    return data[0]


# ── POST /risk/metrics/run ────────────────────────────────────────────────────

@router.post("/metrics/run")
async def run_metrics():
    """Manually trigger nightly metrics computation."""
    try:
        metrics = await run_nightly_metrics()
    except RiskAgentError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        logger.exception("Unexpected error in /risk/metrics/run")
        raise HTTPException(status_code=500, detail=str(exc))

    if metrics is None:
        return {"detail": "Insufficient closed positions for metrics."}
    return metrics.model_dump()


# ── GET /risk/status ──────────────────────────────────────────────────────────

@router.get("/status")
async def get_status():
    """
    Return a lightweight RiskStatus summary.
    Combines alert counts, latest metrics date, autonomous suspension flag, and regime.
    """
    supabase = _get_client()

    # Active alerts
    alerts_resp = (
        supabase
        .table("risk_alerts")
        .select("id,tier,severity")
        .eq("resolved", False)
        .execute()
    )
    active = alerts_resp.data or []

    critical_count = sum(1 for a in active if a.get("severity") == "CRITICAL")
    breach_count = sum(1 for a in active if a.get("severity") == "BREACH")
    warn_count = sum(1 for a in active if a.get("severity") == "WARN")
    autonomous_suspended = critical_count > 0

    # Latest metrics date
    metrics_resp = (
        supabase
        .table("portfolio_metrics")
        .select("date")
        .order("date", desc=True)
        .limit(1)
        .execute()
    )
    latest_metrics_date = None
    if metrics_resp.data:
        latest_metrics_date = metrics_resp.data[0].get("date")

    # Current regime
    regime = None
    try:
        regime_resp = (
            supabase
            .table("macro_briefings")
            .select("regime")
            .order("date", desc=True)
            .limit(1)
            .execute()
        )
        if regime_resp.data:
            regime = regime_resp.data[0]["regime"]
    except Exception:
        pass

    status = RiskStatus(
        active_alerts_count=len(active),
        critical_count=critical_count,
        breach_count=breach_count,
        warn_count=warn_count,
        latest_metrics_date=str(latest_metrics_date) if latest_metrics_date else None,
        autonomous_mode_suspended=autonomous_suspended,
        regime=regime,
    )
    return status.model_dump()


# ── GET /risk/stops ───────────────────────────────────────────────────────────

@router.get("/stops")
async def get_stop_levels():
    """
    Return the current stop price and threshold for every OPEN position,
    adjusted for the active macro regime.
    """
    supabase = _get_client()

    positions_resp = (
        supabase
        .table("positions")
        .select("id,ticker,direction,entry_price,current_price,pnl_pct,stop_loss_price,sector")
        .eq("status", "OPEN")
        .execute()
    )
    positions = positions_resp.data or []

    # Current regime
    regime = "Transitional"
    try:
        regime_resp = (
            supabase
            .table("macro_briefings")
            .select("regime")
            .order("date", desc=True)
            .limit(1)
            .execute()
        )
        if regime_resp.data:
            regime = regime_resp.data[0]["regime"]
    except Exception:
        pass

    t1_thresh = _tier1_threshold(regime)

    result = []
    for pos in positions:
        entry = pos.get("entry_price")
        try:
            entry_f = float(entry) if entry is not None else None
        except (TypeError, ValueError):
            entry_f = None

        computed_stop = round(entry_f * (1 + t1_thresh), 4) if entry_f else None

        result.append({
            "id": pos.get("id"),
            "ticker": pos.get("ticker"),
            "direction": pos.get("direction"),
            "entry_price": entry_f,
            "current_price": pos.get("current_price"),
            "pnl_pct": pos.get("pnl_pct"),
            "stop_loss_price": pos.get("stop_loss_price") or computed_stop,
            "tier1_threshold_pct": t1_thresh * 100,
            "regime": regime,
        })

    return result


# ── POST /risk/monitor/run ────────────────────────────────────────────────────

@router.post("/monitor/run")
async def run_monitor():
    """Manually trigger one risk monitor cycle — bypasses market-hours guard."""
    try:
        supabase = _get_supabase()
        regime = _read_macro_regime(supabase)
        # Call run_monitor_cycle directly so the market-hours guard is skipped
        # for manual/test runs. The scheduled job uses run_risk_monitor() which
        # respects the guard.
        result = run_monitor_cycle(supabase, regime, force=True)
    except RiskAgentError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        logger.exception("Unexpected error in /risk/monitor/run")
        raise HTTPException(status_code=500, detail=str(exc))
    return result
