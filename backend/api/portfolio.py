"""
Portfolio Construction & Sizing API — FastAPI router.

Endpoints:
  POST /portfolio/size              — trigger Kelly sizing for a memo_id
  GET  /portfolio/positions         — all OPEN positions
  GET  /portfolio/pending           — all PENDING_APPROVAL recommendations
  GET  /portfolio/exposure          — current gross/net/sector exposure summary
  POST /portfolio/approve/{id}      — human approves a PENDING_APPROVAL record
  POST /portfolio/reject/{id}       — human rejects a PENDING_APPROVAL record
  GET  /portfolio/history           — CLOSED/REJECTED positions (default last 30 days)
"""

from dotenv import load_dotenv

load_dotenv()

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from pydantic import BaseModel

from backend.agents.portfolio_agent import PortfolioAgentError, run_portfolio_sizing
from backend.memory.vector_store import _get_client
from backend.portfolio.exposure_tracker import REGIME_CAPS, get_current_exposure
from backend.notifications.events import notify_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


# ── Request models ────────────────────────────────────────────────────────────


class SizeRequest(BaseModel):
    memo_id: str
    portfolio_value: Optional[float] = None  # resolved from IBKR if not provided


# ── POST /portfolio/size ──────────────────────────────────────────────────────


@router.post("/size")
async def size_position(body: SizeRequest):
    """
    Trigger Kelly sizing for a completed InvestmentMemo.

    Runs the full 5-phase portfolio sizing pipeline and returns a
    SizingRecommendation persisted to the positions table as PENDING_APPROVAL.
    """
    try:
        rec = await run_portfolio_sizing(
            memo_id=body.memo_id,
            portfolio_value=body.portfolio_value,
        )
    except PortfolioAgentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Unexpected error in /portfolio/size")
        raise HTTPException(status_code=500, detail=f"Portfolio sizing error: {exc}")

    return rec.model_dump()


# ── GET /portfolio/positions ──────────────────────────────────────────────────


@router.get("/positions")
def get_open_positions():
    """Return all OPEN positions from the positions table."""
    try:
        client = _get_client()
        result = (
            client.table("positions")
            .select("*")
            .eq("status", "OPEN")
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Supabase error: {exc}")

    return result.data or []


# ── GET /portfolio/pending ────────────────────────────────────────────────────


@router.get("/pending")
def get_pending_positions():
    """Return all PENDING_APPROVAL sizing recommendations awaiting human review."""
    try:
        client = _get_client()
        result = (
            client.table("positions")
            .select("*")
            .eq("status", "PENDING_APPROVAL")
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Supabase error: {exc}")

    return result.data or []


# ── GET /portfolio/exposure ───────────────────────────────────────────────────


@router.get("/exposure")
def get_exposure(portfolio_value: Optional[float] = Query(None, gt=0)):
    """
    Return current gross/net/sector exposure summary.

    Reads all OPEN positions and the latest macro regime, then computes
    live exposure fractions and compares them against regime-gated caps.
    portfolio_value is resolved from IBKR NetLiquidation if not provided.
    """
    if portfolio_value is None or portfolio_value <= 0:
        from backend.broker.ibkr import get_portfolio_value as _get_pv
        portfolio_value = _get_pv()

    try:
        client = _get_client()

        # Fetch OPEN positions
        pos_result = (
            client.table("positions")
            .select("*")
            .eq("status", "OPEN")
            .execute()
        )
        open_positions = pos_result.data or []

        # Fetch current regime
        regime_result = (
            client.table("macro_briefings")
            .select("regime")
            .order("date", desc=True)
            .limit(1)
            .execute()
        )
        regime = "Risk-On"
        if regime_result.data:
            regime = regime_result.data[0].get("regime", "Risk-On")

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Supabase error: {exc}")

    exposure = get_current_exposure(open_positions, portfolio_value, regime)
    caps = REGIME_CAPS.get(regime, REGIME_CAPS["Risk-On"])

    return {
        "regime": regime,
        "gross_exposure_pct": exposure["gross_exposure_pct"],
        "net_exposure_pct": exposure["net_exposure_pct"],
        "position_count": exposure["position_count"],
        "sector_concentration": exposure["sector_concentration"],
        "caps": caps,
    }


# ── POST /portfolio/approve/{position_id} ─────────────────────────────────────


@router.post("/approve/{position_id}")
def approve_position(position_id: str):
    """
    Approve a PENDING_APPROVAL sizing recommendation.

    Sets status = 'APPROVED'. In supervised mode the execution agent will
    pick up APPROVED records and route them to IBKR.
    """
    try:
        client = _get_client()

        # ── CRITICAL alert gate ───────────────────────────────────────────────
        try:
            critical_resp = (
                client.table("risk_alerts")
                .select("id,trigger")
                .eq("severity", "CRITICAL")
                .eq("resolved", False)
                .execute()
            )
            if critical_resp.data:
                triggers = [a.get("trigger", "unknown") for a in critical_resp.data]
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Approval blocked: {len(critical_resp.data)} unresolved CRITICAL "
                        f"alert(s). Resolve all CRITICAL alerts before approving trades. "
                        f"Triggers: {'; '.join(triggers)}"
                    ),
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("CRITICAL alert check failed (non-blocking): %s", exc)

        result = (
            client.table("positions")
            .update({"status": "APPROVED"})
            .eq("id", position_id)
            .eq("status", "PENDING_APPROVAL")
            .execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Supabase error: {exc}")

    if not result.data:
        raise HTTPException(
            status_code=404,
            detail=f"Position {position_id!r} not found or not in PENDING_APPROVAL state",
        )

    logger.info("Position %s approved", position_id)
    pos = result.data[0] if result.data else {}
    notify_event("POSITION_APPROVED", {
        "ticker": pos.get("ticker", "—"),
        "size_label": pos.get("size_label", "—"),
        "dollar_size": pos.get("dollar_size", 0),
        "share_count": pos.get("share_count", "—"),
        "entry_price": pos.get("entry_price", "—"),
    })
    return {"status": "approved", "position_id": position_id}


# ── POST /portfolio/reject/{position_id} ──────────────────────────────────────


@router.post("/reject/{position_id}")
def reject_position(position_id: str):
    """
    Reject a PENDING_APPROVAL sizing recommendation.

    Sets status = 'REJECTED'. The record is preserved for audit / analysis.
    """
    try:
        client = _get_client()
        result = (
            client.table("positions")
            .update({"status": "REJECTED"})
            .eq("id", position_id)
            .eq("status", "PENDING_APPROVAL")
            .execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Supabase error: {exc}")

    if not result.data:
        raise HTTPException(
            status_code=404,
            detail=f"Position {position_id!r} not found or not in PENDING_APPROVAL state",
        )

    logger.info("Position %s rejected", position_id)
    pos = result.data[0] if result.data else {}
    notify_event("POSITION_REJECTED", {
        "ticker": pos.get("ticker", "—"),
        "position_id": position_id,
    })
    return {"status": "rejected", "position_id": position_id}


# ── GET /portfolio/history ────────────────────────────────────────────────────


@router.get("/history")
def get_position_history(days: int = Query(30, ge=1, le=365)):
    """
    Return CLOSED and REJECTED positions from the last *days* days.

    Useful for reviewing past sizing decisions and P&L attribution.
    """
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()

    try:
        client = _get_client()
        result = (
            client.table("positions")
            .select("*")
            .in_("status", ["CLOSED", "REJECTED"])
            .gte("created_at", cutoff)
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Supabase error: {exc}")

    return result.data or []
