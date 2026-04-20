"""
Execution Agent — APPROVED positions → IBKR orders → OPEN positions.

Runs every 5 minutes via APScheduler. Polls Supabase for APPROVED positions,
checks CRITICAL risk alerts, places IBKR orders, registers fill handler, and
classifies outcomes. Per-cycle connect/disconnect to IBKR.

Entry point: run_execution_cycle(force=False)
"""

from dotenv import load_dotenv

load_dotenv()

import asyncio
import logging
from datetime import datetime, time
from typing import List

import pytz

from backend.memory.vector_store import _get_client
from backend.broker import ibkr as _ibkr
from backend.broker import order_builder as _order_builder
from backend.broker import order_manager as _order_manager
from backend.broker import fill_recorder as _fill_recorder
from backend.broker.schemas import ExecutionSummary
from backend.broker.ibkr import IBKRConnectionError
from backend.broker.order_builder import OrderBuildError
from backend.broker.order_manager import OrderManagerError
from backend.notifications.events import notify_event

logger = logging.getLogger(__name__)
_ET = pytz.timezone("America/New_York")


class ExecutionAgentError(Exception):
    pass


# ── Helpers ───────────────────────────────────────────────────────────────────


def _is_market_open() -> bool:
    """True during regular US equity trading hours (Mon–Fri, 09:30–15:55 ET)."""
    now_et = datetime.now(_ET)
    if now_et.weekday() not in {0, 1, 2, 3, 4}:
        return False
    t = now_et.time()
    return time(9, 30) <= t < time(15, 55)


def _has_critical_alerts() -> bool:
    """Return True if any unresolved CRITICAL risk alerts exist in Supabase."""
    try:
        resp = (
            _get_client()
            .table("risk_alerts")
            .select("id")
            .eq("severity", "CRITICAL")
            .eq("resolved", False)
            .execute()
        )
        return len(resp.data or []) > 0
    except Exception as exc:
        logger.warning("CRITICAL alert check failed (non-blocking): %s", exc)
        return False  # Don't block execution if the check itself fails


# ── Reconciliation helper ─────────────────────────────────────────────────────

def run_fill_recon() -> int:
    """
    Query IBKR for all fills in the current session and reconcile them with
    the Supabase fills table.

    This catches fills that were missed due to disconnections or if the
    real-time callback didn't fire before the previous cycle disconnected.

    Returns:
        Number of fills processed.
    """
    try:
        ib = _ibkr.connect()
        loop = _ibkr.get_loop()

        # ib.fills() must be called on the dedicated loop thread.
        async def _get_fills():
            return ib.fills()

        future = asyncio.run_coroutine_threadsafe(_get_fills(), loop)
        fills = future.result(timeout=10)

        if not fills:
            return 0

        logger.info("Reconciling %d fills from IBKR...", len(fills))
        count = 0
        for fill in fills:
            # fill.execution.permId identifies the order
            perm_id = fill.execution.permId
            if not perm_id:
                continue

            # Pass to fill_recorder for idempotent processing
            _fill_recorder.handle_exec_detail(trade=None, fill=fill, perm_id_override=perm_id)
            count += 1

        return count
    except Exception as exc:
        logger.warning("Fill reconciliation failed: %s", exc)
        return 0


# ── Main entry point ──────────────────────────────────────────────────────────


def run_execution_cycle(force: bool = False) -> ExecutionSummary:
    """
    One execution cycle. Called every 5 minutes by APScheduler.

    Args:
        force: If True, bypass the market-hours guard (used by manual API trigger).

    Returns:
        ExecutionSummary with per-cycle metrics.
    """
    cycle_start = datetime.utcnow().isoformat()
    summary = ExecutionSummary(cycle_at=cycle_start)

    # ── A: Market hours guard ─────────────────────────────────────────────────
    if not force and not _is_market_open():
        logger.debug("Execution cycle skipped — market closed")
        summary.skipped_market_closed = True
        return summary

    # ── B: CRITICAL alert gate ────────────────────────────────────────────────
    if _has_critical_alerts():
        logger.warning("Execution cycle blocked: unresolved CRITICAL risk alert(s)")
        summary.critical_blocked = True
        notify_event("EXECUTION_BLOCKED", {"critical_count": "1+"})
        return summary

    client = _get_client()
    this_cycle_order_ids: List[str] = []

    try:
        # ── C: Reconciliation ─────────────────────────────────────────────────
        # Catch any fills missed by the previous cycle's real-time handler.
        recon_count = run_fill_recon()
        if recon_count > 0:
            logger.info("Fill reconciliation complete: processed %d executions", recon_count)

        # ── D: Handle timed-out orders ────────────────────────────────────────
        # check_timeouts() sets PARTIAL_FILLED (has fills) or TIMEOUT (zero fills).
        # REJECTED is reserved for explicit IBKR error callbacks — never timeout.
        timed_out_position_ids = _order_manager.check_timeouts()
        for pos_id in timed_out_position_ids:
            order_result = (
                client.table("orders")
                .select("*")
                .eq("position_id", pos_id)
                .in_("status", ["PARTIAL_FILLED", "TIMEOUT"])
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if not order_result.data:
                continue
            order_row = order_result.data[0]
            order_status = order_row.get("status")
            total_filled = float(order_row.get("total_filled_qty") or 0)

            if order_status == "PARTIAL_FILLED" and total_filled > 0:
                # Partial fill — open position at actual filled quantity + avg fill price
                _fill_recorder.record_partial_fill_open(order_row["id"])
                logger.info(
                    "Position %s opened at partial fill (%.0f shares, status=PARTIAL_FILLED)",
                    pos_id, total_filled,
                )
            else:
                # Zero fill (TIMEOUT) — revert to APPROVED so next cycle can retry
                client.table("positions").update({"status": "APPROVED"}).eq("id", pos_id).execute()
                logger.info(
                    "Position %s reverted to APPROVED after zero-fill timeout (status=%s)",
                    pos_id, order_status,
                )

            summary.orders_timeout += 1

        # ── D: Fetch APPROVED positions ───────────────────────────────────────
        approved_result = (
            client.table("positions")
            .select("*")
            .eq("status", "APPROVED")
            .order("created_at")
            .execute()
        )
        approved = approved_result.data or []
        summary.approved_found = len(approved)

        if not approved:
            logger.debug("No APPROVED positions — cycle complete")
            return summary

        # ── E + F: Check for existing active orders, then build + place ───────
        for pos in approved:
            # E: Skip if an active order already exists (avoids duplicate orders)
            existing = (
                client.table("orders")
                .select("id")
                .eq("position_id", pos["id"])
                .in_("status", ["SUBMITTED", "PARTIAL"])
                .execute()
            )
            if existing.data:
                logger.debug(
                    "Position %s (%s) already has an active order — skipping",
                    pos["id"], pos.get("ticker"),
                )
                continue

            # F: Build and place
            try:
                req, contract, ib_order = _order_builder.build_order(pos)
                order_status = _order_manager.place_order(req, contract, ib_order)
                this_cycle_order_ids.append(order_status.order_id)
                summary.orders_placed += 1
                logger.info(
                    "Order placed: %s %s | type=%s qty=%d order_id=%s",
                    pos.get("ticker"), pos["id"],
                    req.order_type, req.requested_qty, order_status.order_id,
                )

            except IBKRConnectionError as exc:
                logger.error("IBKR connection failed for %s: %s", pos.get("ticker"), exc)
                summary.orders_error += 1
                summary.errors.append(f"{pos.get('ticker')}: IBKR connection error — {exc}")
                notify_event("IBKR_CONNECTION_ERROR", {"ticker": pos.get("ticker", "—"), "error": str(exc)})
                break  # IBKR is down; don't try more orders this cycle

            except OrderBuildError as exc:
                logger.warning("Order build failed for %s: %s", pos.get("ticker"), exc)
                summary.orders_error += 1
                summary.errors.append(f"{pos.get('ticker')}: build error — {exc}")
                notify_event("ORDER_ERROR", {"ticker": pos.get("ticker", "—"), "error": f"Build error: {exc}"})
                continue

            except (OrderManagerError, Exception) as exc:
                logger.error("Order placement failed for %s: %s", pos.get("ticker"), exc)
                summary.orders_error += 1
                summary.errors.append(f"{pos.get('ticker')}: placement error — {exc}")
                notify_event("ORDER_ERROR", {"ticker": pos.get("ticker", "—"), "error": f"Placement error: {exc}"})
                continue

        # ── G: Register fill handler and wait 60s for fill events ─────────────
        if this_cycle_order_ids:
            try:
                ib = _ibkr._get_ib()
                loop = _ibkr.get_loop()

                def _add_handler():
                    ib.execDetailsEvent += _fill_recorder.handle_exec_detail

                def _remove_handler():
                    ib.execDetailsEvent -= _fill_recorder.handle_exec_detail

                # Ensure handler mutation happens on the IBKR loop thread.
                loop.call_soon_threadsafe(_add_handler)
                # Wait 60s on the IBKR loop without requiring a local event loop.
                asyncio.run_coroutine_threadsafe(asyncio.sleep(60), loop).result(timeout=70)
                loop.call_soon_threadsafe(_remove_handler)
            except IBKRConnectionError as exc:
                logger.warning("Could not register fill handler: %s", exc)

        # ── H: Classify this cycle's orders ───────────────────────────────────
        for order_id in this_cycle_order_ids:
            status = _order_manager.get_order_status(order_id)
            if status is None:
                continue
            if status.status == "FILLED":
                summary.orders_filled += 1
                o_result = (
                    client.table("orders")
                    .select("position_id")
                    .eq("id", order_id)
                    .execute()
                )
                if o_result.data:
                    summary.position_ids_filled.append(o_result.data[0]["position_id"])
            elif status.status == "PARTIAL":
                summary.orders_partial += 1

    except Exception as exc:
        logger.error("Unhandled execution cycle error: %s", exc)
        summary.errors.append(f"Cycle error: {exc}")

    finally:
        # ── I: Keep the connection alive — it is a shared singleton used by
        # get_portfolio_value(), risk monitor, and account summary endpoints.
        # Only disconnect if orders were actually placed (fill handler registered)
        # so the session can be cleanly re-established on the next cycle.
        if this_cycle_order_ids:
            _ibkr.disconnect()

    logger.info(
        "Execution cycle complete — approved=%d placed=%d filled=%d partial=%d timeout=%d error=%d",
        summary.approved_found, summary.orders_placed, summary.orders_filled,
        summary.orders_partial, summary.orders_timeout, summary.orders_error,
    )
    if summary.orders_placed or summary.orders_filled or summary.orders_error or summary.orders_timeout:
        notify_event("EXECUTION_CYCLE_COMPLETE", {
            "orders_placed":  summary.orders_placed,
            "orders_filled":  summary.orders_filled,
            "orders_partial": summary.orders_partial,
            "orders_timeout": summary.orders_timeout,
            "orders_error":   summary.orders_error,
        })
    return summary
