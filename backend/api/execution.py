"""
Execution Agent API — FastAPI router.

Endpoints:
  GET  /execution/orders              — list recent orders (filter by status)
  GET  /execution/orders/{order_id}   — single order + its fills
  GET  /execution/fills               — list fills (filter by ticker)
  POST /execution/cancel/{order_id}   — cancel a live IBKR order
  GET  /execution/status              — IBKR connection status + active order count
  POST /execution/cycle/run           — manually trigger one execution cycle
"""

from dotenv import load_dotenv

load_dotenv()

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException

from backend.memory.vector_store import _get_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/execution", tags=["execution"])


# ── GET /execution/orders ─────────────────────────────────────────────────────


@router.get("/orders")
async def list_orders(status: Optional[str] = None, limit: int = 50):
    """List orders, newest first. Optionally filter by status."""
    def _run():
        try:
            client = _get_client()
            query = (
                client.table("orders")
                .select("*")
                .order("created_at", desc=True)
                .limit(limit)
            )
            if status:
                query = query.eq("status", status.upper())
            result = query.execute()
            return result.data or []
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    return await asyncio.to_thread(_run)


# ── GET /execution/orders/{order_id} ─────────────────────────────────────────


@router.get("/orders/{order_id}")
async def get_order(order_id: str):
    """Return a single order and all of its fills."""
    def _run():
        try:
            client = _get_client()
            order_result = (
                client.table("orders").select("*").eq("id", order_id).execute()
            )
            if not order_result.data:
                raise HTTPException(
                    status_code=404, detail=f"Order {order_id!r} not found"
                )
            fills_result = (
                client.table("fills")
                .select("*")
                .eq("order_id", order_id)
                .order("fill_time")
                .execute()
            )
            return {"order": order_result.data[0], "fills": fills_result.data or []}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    return await asyncio.to_thread(_run)


# ── GET /execution/fills ──────────────────────────────────────────────────────


@router.get("/fills")
async def list_fills(ticker: Optional[str] = None, limit: int = 100):
    """List fills, newest first. Optionally filter by ticker."""
    def _run():
        try:
            client = _get_client()
            query = (
                client.table("fills")
                .select("*")
                .order("fill_time", desc=True)
                .limit(limit)
            )
            if ticker:
                query = query.eq("ticker", ticker.upper())
            result = query.execute()
            return result.data or []
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    return await asyncio.to_thread(_run)


# ── POST /execution/cancel/{order_id} ────────────────────────────────────────


@router.post("/cancel/{order_id}")
async def cancel_order(order_id: str):
    """Cancel a live IBKR order and update its status to CANCELLED."""
    def _run():
        from backend.broker.order_manager import cancel_order as _cancel
        try:
            cancelled = _cancel(order_id)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        if not cancelled:
            raise HTTPException(
                status_code=409,
                detail=f"Order {order_id!r} not found or already in a terminal state",
            )
        return {"cancelled": True, "order_id": order_id}

    return await asyncio.to_thread(_run)


# ── GET /execution/status ─────────────────────────────────────────────────────


@router.get("/status")
async def execution_status():
    """IBKR connection status, paper/live mode, and active order count."""
    def _run():
        from backend.broker import ibkr as _ibkr

        ibkr_connected = False
        try:
            if _ibkr._ib is not None and _ibkr._ib.isConnected():
                ibkr_connected = True
            else:
                _ibkr.connect()
                ibkr_connected = True
        except Exception:
            ibkr_connected = False

        active_orders = 0
        try:
            client = _get_client()
            result = (
                client.table("orders")
                .select("id", count="exact")
                .in_("status", ["SUBMITTED", "PARTIAL"])
                .execute()
            )
            active_orders = result.count or 0
        except Exception:
            pass

        account = {}
        if ibkr_connected:
            try:
                from backend.broker.ibkr import get_account_summary
                account = get_account_summary()
            except Exception:
                pass

        return {
            "ibkr_connected": ibkr_connected,
            "is_paper": _ibkr.is_paper(),
            "active_orders": active_orders,
            "net_liquidation": account.get("NetLiquidation"),
            "cash": account.get("TotalCashValue"),
            "unrealized_pnl": account.get("UnrealizedPnL"),
            "realized_pnl": account.get("RealizedPnL"),
        }

    return await asyncio.to_thread(_run)


# ── POST /execution/cycle/run ─────────────────────────────────────────────────


@router.post("/cycle/run")
async def run_cycle_manual():
    """
    Manually trigger one execution cycle.
    Bypasses the market-hours guard (force=True).
    """
    import asyncio
    from backend.agents.execution_agent import run_execution_cycle

    try:
        summary = await asyncio.to_thread(run_execution_cycle, True)
        return summary.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
