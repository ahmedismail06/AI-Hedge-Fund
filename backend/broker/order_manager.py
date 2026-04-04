"""
Order Manager — Supabase CRUD for the orders table + IBKR order placement.

Owns the full lifecycle from submission through terminal states (FILLED,
CANCELLED, TIMEOUT, ERROR). Does NOT handle individual fill callbacks — that
is fill_recorder's responsibility.

Public API:
    place_order(req, contract, ib_order) -> OrderStatus
    cancel_order(order_id)               -> bool
    check_timeouts()                     -> list[str]   (position_ids timed out)
    get_order_status(order_id)           -> Optional[OrderStatus]
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()

from backend.broker.ibkr import IBKRConnectionError, connect, get_loop  # noqa: E402
from backend.broker.schemas import OrderRequest, OrderStatus  # noqa: E402
from backend.memory.vector_store import _get_client  # noqa: E402

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Custom exception
# ──────────────────────────────────────────────────────────────────────────────

TERMINAL_STATUSES = {"FILLED", "CANCELLED", "TIMEOUT", "ERROR"}


class OrderManagerError(Exception):
    """Raised when a Supabase operation in order_manager fails."""


# ──────────────────────────────────────────────────────────────────────────────
# Function 1: place_order
# ──────────────────────────────────────────────────────────────────────────────


def place_order(req: OrderRequest, contract, ib_order) -> OrderStatus:
    """
    Submit an order to IBKR and persist the initial row to the orders table.

    Args:
        req:      OrderRequest built by order_builder.py from an APPROVED position.
        contract: ib_insync Contract object (Stock, etc.) describing the instrument.
        ib_order: ib_insync Order object (LimitOrder, MarketOrder, etc.).

    Returns:
        OrderStatus with status="SUBMITTED" and the newly created orders table UUID.

    Raises:
        IBKRConnectionError: if _get_ib() cannot establish a connection.
        OrderManagerError:   if the Supabase insert fails.
    """
    # 1. Connect — let IBKRConnectionError propagate to execution_agent.
    ib = connect()

    # 2. Place order and wait for permId on the dedicated ib loop.
    async def _do_place():
        t = ib.placeOrder(contract, ib_order)
        await asyncio.sleep(1)
        return t

    trade = asyncio.run_coroutine_threadsafe(_do_place(), get_loop()).result(timeout=30)

    # 3. Extract permId; treat 0 as unassigned.
    raw_perm_id: int = trade.order.permId
    perm_id: Optional[int] = raw_perm_id if raw_perm_id else None

    # 5. Compute timeout boundary.
    timeout_at: datetime = datetime.utcnow() + timedelta(minutes=req.timeout_minutes)

    # 6. Persist to Supabase orders table.
    now_iso = datetime.utcnow().isoformat()
    row = {
        "position_id": req.position_id,
        "ticker": req.ticker,
        "direction": req.direction,
        "order_type": req.order_type,
        "requested_qty": req.requested_qty,
        "limit_price": req.limit_price,
        "ibkr_order_id": perm_id,
        "status": "SUBMITTED",
        "total_filled_qty": 0,
        "submitted_at": now_iso,
        "timeout_at": timeout_at.isoformat(),
    }

    try:
        result = _get_client().table("orders").insert(row).execute()
        order_db_id: str = result.data[0]["id"]
    except Exception as exc:
        logger.error(
            "Supabase insert failed for order (ticker=%s, position_id=%s): %s",
            req.ticker,
            req.position_id,
            exc,
        )
        raise OrderManagerError(
            f"Failed to insert order row for {req.ticker}: {exc}"
        ) from exc

    # 7. Audit log.
    logger.info(
        "Order placed for %s: db_id=%s, ibkr_perm_id=%s",
        req.ticker,
        order_db_id,
        perm_id,
    )

    # 8. Return lightweight status object.
    return OrderStatus(
        order_id=order_db_id,
        ibkr_order_id=perm_id,
        status="SUBMITTED",
        submitted_at=now_iso,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Function 2: cancel_order
# ──────────────────────────────────────────────────────────────────────────────


def cancel_order(order_id: str) -> bool:
    """
    Cancel an open order both in IBKR and in the orders table.

    Returns True if the cancellation was actioned, False if the order was not
    found or was already in a terminal state (FILLED/CANCELLED/TIMEOUT/ERROR).

    Never raises — logs errors to stderr and returns False on failure.
    """
    # 1. Fetch the row.
    try:
        result = (
            _get_client().table("orders").select("*").eq("id", order_id).execute()
        )
    except Exception as exc:
        logger.error(
            "Supabase select failed in cancel_order (order_id=%s): %s", order_id, exc
        )
        return False

    if not result.data:
        logger.warning("cancel_order: order_id=%s not found in orders table", order_id)
        return False

    row = result.data[0]

    # 2. No-op for terminal orders.
    if row.get("status") in TERMINAL_STATUSES:
        logger.info(
            "cancel_order: order_id=%s already in terminal state %s — skipping",
            order_id,
            row.get("status"),
        )
        return False

    # 3. Cancel in IBKR if we have a permId.
    ibkr_order_id: Optional[int] = row.get("ibkr_order_id")
    if ibkr_order_id is not None:
        try:
            ib = connect()

            async def _do_cancel():
                for t in ib.trades():
                    if t.order.permId == ibkr_order_id:
                        ib.cancelOrder(t.order)
                        logger.info(
                            "Sent cancel to IBKR for order_id=%s, perm_id=%s",
                            order_id,
                            ibkr_order_id,
                        )
                        break

            asyncio.run_coroutine_threadsafe(_do_cancel(), get_loop()).result(timeout=10)
        except IBKRConnectionError as exc:
            # IBKR unavailable — still mark CANCELLED in Supabase so the
            # execution cycle does not keep retrying this order.
            logger.warning(
                "IBKR unreachable during cancel_order (order_id=%s): %s — "
                "updating Supabase to CANCELLED anyway",
                order_id,
                exc,
            )
        except Exception as exc:
            logger.error(
                "Unexpected error cancelling IBKR order (order_id=%s, perm_id=%s): %s",
                order_id,
                ibkr_order_id,
                exc,
            )

    # 4. Update Supabase to PENDING (re-queue after manual cancel).
    try:
        _get_client().table("orders").update(
            {
                "status": "PENDING",
                "cancelled_at": datetime.utcnow().isoformat(),
                "ibkr_order_id": None,
                "submitted_at": None,
            }
        ).eq("id", order_id).execute()
    except Exception as exc:
        logger.error(
            "Supabase update to PENDING failed (order_id=%s): %s", order_id, exc
        )
        return False

    logger.info("Order cancelled and reset to PENDING: order_id=%s", order_id)
    return True


# ──────────────────────────────────────────────────────────────────────────────
# Function 3: check_timeouts
# ──────────────────────────────────────────────────────────────────────────────


def check_timeouts() -> List[str]:
    """
    Identify open orders whose timeout_at has passed, cancel them in IBKR, and
    mark them TIMEOUT in the orders table.

    Called at the start of each execution cycle to prevent stale orders from
    blocking position re-evaluation.

    Returns:
        List of position_id strings for every order that was timed out.
        Empty list if no timeouts occurred or if the Supabase query failed.
    """
    now_iso = datetime.utcnow().isoformat()
    timed_out_position_ids: List[str] = []

    # 1. Query open orders past their timeout boundary.
    try:
        result = (
            _get_client()
            .table("orders")
            .select("*")
            .in_("status", ["SUBMITTED", "PARTIAL"])
            .lt("timeout_at", now_iso)
            .execute()
        )
    except Exception as exc:
        logger.error("Supabase query failed in check_timeouts: %s", exc)
        return timed_out_position_ids

    expired_orders = result.data or []

    for order in expired_orders:
        order_id: str = order["id"]
        position_id: str = order.get("position_id", "")
        ibkr_order_id: Optional[int] = order.get("ibkr_order_id")

        # 2a. Attempt IBKR cancellation for this order.
        if ibkr_order_id is not None:
            try:
                ib = connect()

                async def _do_timeout_cancel():
                    for t in ib.trades():
                        if t.order.permId == ibkr_order_id:
                            ib.cancelOrder(t.order)
                            break

                asyncio.run_coroutine_threadsafe(_do_timeout_cancel(), get_loop()).result(timeout=10)
            except IBKRConnectionError as exc:
                logger.warning(
                    "IBKR unreachable while timing out order_id=%s: %s",
                    order_id,
                    exc,
                )
            except Exception as exc:
                logger.error(
                    "Error cancelling timed-out order in IBKR (order_id=%s): %s",
                    order_id,
                    exc,
                )

        # 2b. Mark TIMEOUT in Supabase.
        try:
            _get_client().table("orders").update({"status": "TIMEOUT"}).eq(
                "id", order_id
            ).execute()
        except Exception as exc:
            logger.error(
                "Supabase update to TIMEOUT failed (order_id=%s): %s", order_id, exc
            )
            # Continue processing remaining expired orders.
            continue

        logger.warning(
            "Order %s timed out for position %s", order_id, position_id
        )
        timed_out_position_ids.append(position_id)

    return timed_out_position_ids


# ──────────────────────────────────────────────────────────────────────────────
# Function 4: get_order_status
# ──────────────────────────────────────────────────────────────────────────────


def get_order_status(order_id: str) -> Optional[OrderStatus]:
    """
    Read the current state of an order from the orders table.

    Returns None if the order_id does not exist or if the Supabase read fails.
    Used by the execution API endpoint (GET /orders/{order_id}).
    """
    try:
        result = (
            _get_client().table("orders").select("*").eq("id", order_id).execute()
        )
    except Exception as exc:
        logger.error(
            "Supabase select failed in get_order_status (order_id=%s): %s",
            order_id,
            exc,
        )
        return None

    if not result.data:
        return None

    row = result.data[0]

    return OrderStatus(
        order_id=row["id"],
        ibkr_order_id=row.get("ibkr_order_id"),
        status=row["status"],
        total_filled_qty=float(row.get("total_filled_qty") or 0),
        avg_fill_price=row.get("avg_fill_price"),
        submitted_at=row.get("submitted_at"),
        filled_at=row.get("filled_at"),
    )
