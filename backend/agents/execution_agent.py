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

# Max TIMEOUT orders before a position is abandoned rather than retried.
_MAX_ENTRY_RETRIES = 5


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


def _is_pre_market() -> bool:
    """True during pre-market window (Mon–Fri, 07:00–09:29 ET).

    Used to allow exit-only cycles with outsideRth=True so crisis / pre-earnings
    CLOSE orders reach the exchange before the 9:30 AM open and get queue priority.
    """
    now_et = datetime.now(_ET)
    if now_et.weekday() not in {0, 1, 2, 3, 4}:
        return False
    t = now_et.time()
    return time(7, 0) <= t < time(9, 30)


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


# ── Expiry re-queue helper ────────────────────────────────────────────────────

def _requeue_memo_after_expiry(
    client,
    memo_id: str,
    position: dict,
    timeout_orders: list,
) -> None:
    """
    After a position is marked EXPIRED, reset its memo to PENDING_PM_REVIEW and
    inject an execution_context block so the PM understands why the entry failed.
    The PM can then decide to retry at current price, resize, or pass entirely.
    """
    try:
        memo_resp = client.table("memos").select("memo_json").eq("id", memo_id).single().execute()
        if not memo_resp.data:
            logger.warning("_requeue_memo_after_expiry: memo %s not found — skipping re-queue", memo_id)
            return

        memo_json = memo_resp.data.get("memo_json") or {}
        last_price = None
        if timeout_orders:
            raw = timeout_orders[0].get("limit_price")
            last_price = float(raw) if raw is not None else None

        memo_json["execution_context"] = {
            "status": "REQUEUE_AFTER_EXPIRY",
            "note": (
                f"This position was previously approved but the entry order timed out "
                f"{len(timeout_orders)} time(s) without a single fill. "
                f"The last limit price submitted was ${last_price:.4f} (anchored to live IBKR "
                f"market price at each submission, not the approval-time snapshot). "
                f"Likely cause: stock is illiquid at this order size, the bid-ask spread is too "
                f"wide for a +0.1% limit to fill, or ADV is too low for the requested quantity. "
                f"Re-evaluate: reduce size, widen the limit, or pass."
            ),
            "timeout_count": len(timeout_orders),
            "last_limit_price": last_price,
            "original_entry_price": float(position.get("entry_price") or 0) or None,
            "expired_at": datetime.utcnow().isoformat(),
        }

        client.table("memos").update({
            "memo_json": memo_json,
            "status": "PENDING_PM_REVIEW",
        }).eq("id", memo_id).execute()

        logger.info(
            "Memo %s reset to PENDING_PM_REVIEW with expiry context (%d timeouts, last price $%s) — "
            "PM will re-evaluate %s",
            memo_id, len(timeout_orders), last_price, position.get("ticker"),
        )
    except Exception as exc:
        logger.warning(
            "_requeue_memo_after_expiry failed for memo %s (position %s): %s — position still EXPIRED",
            memo_id, position.get("id"), exc,
        )


# ── Reconciliation helper ─────────────────────────────────────────────────────

def run_fill_recon() -> int:
    """
    Query IBKR for all fills in the current session and reconcile them with
    the Supabase fills table.

    Groups fills by order_id so that the aggregate (VWAP) update happens
    only once per order, rather than for every single fill.

    Returns:
        Number of NEW fills recorded.
    """
    try:
        ib = _ibkr.connect()
        loop = _ibkr.get_loop()

        async def _get_fills():
            # Request today's execution reports from TWS — ib.fills() is
            # session-local and empty on a fresh connection after a restart.
            await ib.reqExecutionsAsync()
            return ib.fills()

        future = asyncio.run_coroutine_threadsafe(_get_fills(), loop)
        fills = future.result(timeout=15)

        if not fills:
            return 0

        logger.info("Reconciling %d fills from IBKR...", len(fills))
        
        # 1. Record new fills (with aggregate update skipped)
        new_fill_count = 0
        orders_to_update = set()
        
        for fill in fills:
            perm_id = fill.execution.permId
            if not perm_id:
                continue

            # Record the fill. handle_exec_detail returns True if it's new.
            is_new = _fill_recorder.handle_exec_detail(
                trade=None, 
                fill=fill, 
                perm_id_override=perm_id,
                skip_aggregate_update=True
            )
            
            if is_new:
                new_fill_count += 1
                orders_to_update.add(perm_id)

        # 2. Update aggregate once for each order that received new fills
        if orders_to_update:
            logger.info("Updating aggregates for %d orders...", len(orders_to_update))
            client = _get_client()
            for perm_id in orders_to_update:
                res = client.table("orders").select("*").eq("ibkr_order_id", perm_id).execute()
                if res.data:
                    order_row = res.data[0]
                    # Passing 0 as new_fill_qty because _update_order_aggregate 
                    # re-queries ALL fills from the DB anyway.
                    _fill_recorder._update_order_aggregate(order_row["id"], order_row, 0)

        return new_fill_count
    except Exception as exc:
        logger.warning("Fill reconciliation failed: %s", exc)
        return 0


# ── Exit cycle ───────────────────────────────────────────────────────────────


def _run_exit_cycle(client, outside_rth: bool = False, skip_position_ids: set | None = None) -> dict:
    """
    Poll OPEN positions with exit_action set and place sell orders.

    Called before new-entry polling so capital is freed before new positions
    are sized. exit_action is cleared only after place_order succeeds — if build
    or placement fails the intent stays in the DB so the next cycle can retry.

    Args:
        outside_rth: If True, passes outsideRth=True on every order so they
                     route during pre-market / extended hours sessions.

    Returns:
        dict with 'exits_placed', 'exits_error', and 'exit_order_ids'.
    """
    result: dict = {"exits_placed": 0, "exits_error": 0, "exit_order_ids": []}

    try:
        exit_result = (
            client.table("positions")
            .select("*")
            .in_("exit_action", ["CLOSE", "TRIM"])
            .eq("status", "OPEN")
            .execute()
        )
        positions_to_exit = exit_result.data or []
    except Exception as exc:
        logger.error("_run_exit_cycle: DB query failed: %s", exc)
        return result

    placed_this_cycle: set = set()
    _skip = skip_position_ids or set()

    for pos in positions_to_exit:
        exit_action = pos.get("exit_action")
        trim_pct = float(pos.get("exit_trim_pct") or 0)
        exit_type = "EXIT_CLOSE" if exit_action == "CLOSE" else "EXIT_TRIM"

        # Guard 0: skip positions whose order was just cancelled this cycle —
        # let the IBKR cancel clear before placing a replacement order.
        if pos["id"] in _skip:
            logger.debug("Position %s just had order cancelled this cycle — deferring exit retry to next cycle", pos["id"])
            continue

        # Guard 1: never process the same position twice in one cycle.
        if pos["id"] in placed_this_cycle:
            logger.warning("_run_exit_cycle: position %s seen twice in same cycle — skipping duplicate", pos["id"])
            continue

        # Guard 2: skip if an active sell order (SUBMITTED or PARTIAL) already exists.
        # Guard 3: skip if a SELL order filled within the last 30 minutes (cycle already executed).
        try:
            from datetime import datetime, timezone, timedelta
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
            existing = (
                client.table("orders")
                .select("id,status,filled_at,created_at")
                .eq("position_id", pos["id"])
                .eq("order_side", "SELL")
                .execute()
            )
            active = [o for o in (existing.data or []) if o["status"] in ("SUBMITTED", "PARTIAL")]
            recent_filled = [
                o for o in (existing.data or [])
                if o["status"] == "FILLED" and o.get("filled_at") and o["filled_at"] >= cutoff
            ]
            if active:
                logger.debug("Position %s already has an active exit order — skipping", pos["id"])
                continue
            if recent_filled:
                logger.info("Position %s had a SELL order filled within 30 min — clearing exit_action and skipping", pos["id"])
                client.table("positions").update({"exit_action": None, "exit_trim_pct": None}).eq("id", pos["id"]).execute()
                continue
            # Guard 4: during pre-market, if a SELL order already timed out within
            # the last 3 hours, hold until market open rather than retrying in a loop.
            # IBKR won't fill illiquid stocks pre-market; each retry just wastes an
            # order submission. At 09:30 _is_market_open() becomes True → guard skips.
            if not _is_market_open():
                timeout_window = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
                recent_timeout = [
                    o for o in (existing.data or [])
                    if o["status"] == "TIMEOUT" and o.get("created_at", "") >= timeout_window
                ]
                if recent_timeout:
                    logger.info(
                        "Pre-market: exit for %s already timed out — holding until market open",
                        pos.get("ticker"),
                    )
                    continue
        except Exception as exc:
            logger.warning("_run_exit_cycle: order check failed for %s: %s", pos.get("ticker"), exc)
            continue

        # Build and place first — only clear exit_action after order is confirmed
        # submitted. This ensures exit_action is never lost to a silent DB-restore
        # failure if build or placement throws.
        try:
            req, contract, ib_order = _order_builder.build_exit_order(
                pos, exit_type, trim_pct, outside_rth=outside_rth
            )
            order_status = _order_manager.place_order(req, contract, ib_order)

            # Order is in IBKR — now safe to clear exit_action.
            try:
                client.table("positions").update({"exit_action": None}).eq("id", pos["id"]).execute()
            except Exception as exc:
                logger.warning("Could not clear exit_action for %s after successful placement: %s", pos.get("ticker"), exc)

            placed_this_cycle.add(pos["id"])
            result["exit_order_ids"].append(order_status.order_id)
            result["exits_placed"] += 1
            logger.info(
                "Exit order placed: %s %s | exit_type=%s qty=%d order_id=%s",
                pos.get("ticker"), pos["id"], exit_type, req.requested_qty, order_status.order_id,
            )
            notify_event("EXIT_ORDER_PLACED", {
                "ticker": pos.get("ticker", "—"),
                "exit_type": exit_type,
                "qty": req.requested_qty,
                "limit_price": req.limit_price,
            })

        except IBKRConnectionError as exc:
            logger.error("IBKR down during exit for %s: %s", pos.get("ticker"), exc)
            result["exits_error"] += 1
            break  # IBKR is gone; don't try more exits this cycle

        except Exception as exc:
            logger.error("Exit order failed for %s: %s", pos.get("ticker"), exc)
            result["exits_error"] += 1

    return result


# ── Add cycle ────────────────────────────────────────────────────────────────


def _run_add_cycle(client, skip_position_ids: set | None = None) -> dict:
    """
    Poll OPEN positions with exit_action=ADD and place buy orders to increase size.

    Uses the same guards as _run_exit_cycle: no active BUY order, no recent
    filled BUY within 30 min. Quantity is derived from exit_trim_pct (same
    normalization as TRIM: 0.35 or 35.0 both mean 35%). If exit_trim_pct is
    absent the position's current share_count is used (i.e. double the position).

    exit_action is cleared only after place_order succeeds so the intent
    survives a failed build or placement and retries next cycle.

    Returns:
        dict with 'adds_placed', 'adds_error', and 'add_order_ids'.
    """
    result: dict = {"adds_placed": 0, "adds_error": 0, "add_order_ids": []}

    try:
        add_result = (
            client.table("positions")
            .select("*")
            .eq("exit_action", "ADD")
            .eq("status", "OPEN")
            .execute()
        )
        positions_to_add = add_result.data or []
    except Exception as exc:
        logger.error("_run_add_cycle: DB query failed: %s", exc)
        return result

    placed_this_cycle: set = set()
    _skip = skip_position_ids or set()

    for pos in positions_to_add:
        if pos["id"] in _skip:
            logger.debug(
                "Position %s just had order cancelled this cycle — deferring ADD retry to next cycle",
                pos["id"],
            )
            continue
        if pos["id"] in placed_this_cycle:
            logger.warning(
                "_run_add_cycle: position %s seen twice in same cycle — skipping duplicate", pos["id"]
            )
            continue

        # Guard: skip if an active ADD order already exists for this position, or one
        # filled within the last 30 minutes (the add already executed this cycle).
        # Filter by exit_type=ENTRY_ADD so the check is direction-agnostic — SHORT adds
        # use order_side=SELL, LONG adds use BUY.
        try:
            from datetime import datetime, timezone, timedelta
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
            existing = (
                client.table("orders")
                .select("id,status,filled_at,created_at")
                .eq("position_id", pos["id"])
                .eq("exit_type", "ENTRY_ADD")
                .execute()
            )
            active = [o for o in (existing.data or []) if o["status"] in ("SUBMITTED", "PARTIAL")]
            recent_filled = [
                o for o in (existing.data or [])
                if o["status"] == "FILLED" and o.get("filled_at") and o["filled_at"] >= cutoff
            ]
            if active:
                logger.debug("Position %s already has an active ADD order — skipping", pos["id"])
                continue
            if recent_filled:
                logger.info(
                    "Position %s had an ADD filled within 30 min — clearing exit_action=ADD", pos["id"]
                )
                client.table("positions").update(
                    {"exit_action": None, "exit_trim_pct": None}
                ).eq("id", pos["id"]).execute()
                continue
        except Exception as exc:
            logger.warning("_run_add_cycle: order check failed for %s: %s", pos.get("ticker"), exc)
            continue

        # Compute add quantity.  exit_trim_pct doubles as the add percentage
        # (same normalization: 0.35 → 35%, 35.0 → 35%).
        add_pct = float(pos.get("exit_trim_pct") or 0)
        total_shares = int(float(pos.get("share_count") or 0))
        if add_pct > 0 and total_shares > 0:
            pct_normalized = add_pct * 100.0 if add_pct <= 1.0 else add_pct
            add_qty = max(1, int(total_shares * pct_normalized / 100))
        elif total_shares > 0:
            add_qty = total_shares  # no pct given — default to matching current size
        else:
            logger.warning(
                "_run_add_cycle: position %s (%s) has no share_count — skipping",
                pos["id"], pos.get("ticker"),
            )
            result["adds_error"] += 1
            continue

        # Build a synthetic row with the add quantity then reuse build_order.
        add_row = dict(pos)
        add_row["share_count"] = add_qty

        try:
            req, contract, ib_order = _order_builder.build_order(add_row)
            # Tag the order so the timeout handler can restore exit_action=ADD
            # on a zero-fill timeout rather than reverting to APPROVED.
            req = req.model_copy(update={"exit_type": "ENTRY_ADD"})
            order_status = _order_manager.place_order(req, contract, ib_order)

            try:
                client.table("positions").update(
                    {"exit_action": None, "exit_trim_pct": None}
                ).eq("id", pos["id"]).execute()
            except Exception as exc:
                logger.warning(
                    "Could not clear exit_action for %s after ADD placement: %s",
                    pos.get("ticker"), exc,
                )

            placed_this_cycle.add(pos["id"])
            result["add_order_ids"].append(order_status.order_id)
            result["adds_placed"] += 1
            logger.info(
                "Add order placed: %s %s | qty=%d order_id=%s",
                pos.get("ticker"), pos["id"], add_qty, order_status.order_id,
            )
            notify_event("ORDER_PLACED", {
                "ticker": pos.get("ticker", "—"),
                "qty": add_qty,
                "note": "ADD to existing position",
            })

        except IBKRConnectionError as exc:
            logger.error("IBKR down during ADD for %s: %s", pos.get("ticker"), exc)
            result["adds_error"] += 1
            break

        except Exception as exc:
            logger.error("Add order failed for %s: %s", pos.get("ticker"), exc)
            result["adds_error"] += 1

    return result


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

    # ── Pre-gate: log pending exit_actions so the gate decision is always visible.
    # exit_action set by the PM agent after 4 PM persists in the DB overnight
    # and is naturally picked up at market open (09:30 ET) — this query confirms
    # positions are in the queue even when the gate below fires and skips execution.
    try:
        _pending = (
            _get_client()
            .table("positions")
            .select("ticker,exit_action")
            .in_("exit_action", ["CLOSE", "TRIM", "ADD"])
            .eq("status", "OPEN")
            .execute()
        )
        _pending_data = _pending.data or []
        if _pending_data:
            _pending_desc = ", ".join(
                f"{p['ticker']}({p['exit_action']})" for p in _pending_data
            )
            logger.info(
                "Action queue: %d position(s) pending — %s",
                len(_pending_data), _pending_desc,
            )
        else:
            logger.debug("Action queue: 0 positions with exit_action set")
    except Exception as _exc:
        logger.warning("Could not query exit queue pre-gate: %s", _exc)

    # ── A: Market hours guard ─────────────────────────────────────────────────
    # All order submission (entries and exits) is restricted to regular market
    # hours: Mon–Fri 09:30–15:55 ET. Nothing is sent to IBKR before the open.
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
    exit_order_ids: List[str] = []

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
        timed_out_this_cycle: set = set()
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
            is_sell = order_row.get("exit_type") in ("EXIT_CLOSE", "EXIT_TRIM")

            if is_sell:
                if order_status == "PARTIAL_FILLED" and total_filled > 0:
                    # Fill callbacks already reduced share_count; just log.
                    logger.info(
                        "Exit order for position %s timed out with partial fill "
                        "(%.0f shares sold) — position share_count already updated by fill handler",
                        pos_id, total_filled,
                    )
                else:
                    # Zero-fill exit timeout — restore exit_action so next cycle retries.
                    exit_type = order_row.get("exit_type") or ""
                    restore_action = "CLOSE" if exit_type == "EXIT_CLOSE" else "TRIM"
                    client.table("positions").update(
                        {"exit_action": restore_action}
                    ).eq("id", pos_id).execute()
                    logger.warning(
                        "Exit order for position %s timed out with zero fills — "
                        "exit_action restored to %s for retry next cycle",
                        pos_id, restore_action,
                    )
            elif order_row.get("exit_type") == "ENTRY_ADD":
                # ADD order timed out — restore exit_action so next cycle retries the buy.
                # Do NOT touch status (position stays OPEN).
                if order_status == "PARTIAL_FILLED" and total_filled > 0:
                    logger.info(
                        "ADD order for position %s timed out with partial fill "
                        "(%.0f shares bought) — fill handler updated share_count",
                        pos_id, total_filled,
                    )
                else:
                    client.table("positions").update({"exit_action": "ADD"}).eq("id", pos_id).execute()
                    logger.warning(
                        "ADD order for position %s timed out with zero fills — "
                        "exit_action restored to ADD for retry next cycle",
                        pos_id,
                    )
            elif order_status == "PARTIAL_FILLED" and total_filled > 0:
                # Entry partial fill — open position at actual filled quantity + avg fill price
                _fill_recorder.record_partial_fill_open(order_row["id"])
                logger.info(
                    "Position %s opened at partial fill (%.0f shares, status=PARTIAL_FILLED)",
                    pos_id, total_filled,
                )
            else:
                # Entry zero fill (TIMEOUT) — revert to APPROVED so next cycle can retry
                client.table("positions").update({"status": "APPROVED"}).eq("id", pos_id).execute()
                logger.info(
                    "Position %s reverted to APPROVED after zero-fill timeout (status=%s) — retry next cycle",
                    pos_id, order_status,
                )

            # Track every position whose IBKR order was just cancelled this cycle.
            # Exit and entry loops below skip these so a replacement order is never
            # submitted before the cancel has cleared in IBKR.
            timed_out_this_cycle.add(pos_id)
            summary.orders_timeout += 1

        # ── E: Exit cycle — sell OPEN positions flagged by PM Agent ──────────
        # Run before new-entry processing so capital is freed before new orders are sized.
        # Pass outside_rth=True during pre-market so crisis/pre-earnings CLOSE orders
        # reach the exchange before the 9:30 AM open when forced via API.
        exit_result = _run_exit_cycle(client, outside_rth=_is_pre_market(), skip_position_ids=timed_out_this_cycle)
        summary.exits_placed = exit_result["exits_placed"]
        summary.exits_error = exit_result["exits_error"]
        exit_order_ids = exit_result["exit_order_ids"]

        # ── E2: Add cycle — buy more shares for OPEN positions flagged by PM Agent ──
        add_result = _run_add_cycle(client, skip_position_ids=timed_out_this_cycle)
        summary.orders_placed += add_result["adds_placed"]
        summary.orders_error += add_result["adds_error"]
        exit_order_ids = exit_order_ids + add_result["add_order_ids"]

        # Guard is already enforced at gate A — this is a belt-and-suspenders check
        # for the force=True path to prevent entries outside market hours.
        if not _is_market_open():
            logger.info("Outside market hours: skipping new entries (%d exit(s) placed)", len(exit_order_ids))
            return summary

        # ── F0: PM halt gate — blocks new entries only; exits/adds already done ──
        # daily_loss_halt_triggered is set by HALT_NEW_ENTRIES crisis decision or
        # intraday drawdown monitor. Check pm_config before processing APPROVED queue.
        try:
            from datetime import timezone as _tz
            _cfg_resp = client.table("pm_config").select(
                "daily_loss_halt_triggered,halted_until"
            ).eq("id", 1).limit(1).execute()
            _cfg = (_cfg_resp.data or [{}])[0]
            if _cfg.get("daily_loss_halt_triggered"):
                _halted_until = _cfg.get("halted_until")
                _still_halted = True
                if _halted_until:
                    try:
                        _halt_dt = datetime.fromisoformat(_halted_until.replace("Z", "+00:00"))
                        _still_halted = _halt_dt > datetime.now(_tz.utc)
                    except Exception:
                        pass
                if _still_halted:
                    logger.warning(
                        "New entries halted (daily_loss_halt_triggered) until %s — "
                        "skipping entry cycle; exits and adds already processed",
                        _halted_until or "indefinite",
                    )
                    return summary
        except Exception as _halt_exc:
            logger.warning("Could not read pm_config halt flag — proceeding with entries: %s", _halt_exc)

        # ── F: Fetch APPROVED positions ───────────────────────────────────────
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
            if not exit_order_ids:
                logger.debug("No APPROVED positions and no exit orders — cycle complete")
                return summary
            logger.debug("No APPROVED positions — proceeding to fill handler for %d exit order(s)", len(exit_order_ids))

        # ── E + F: Check for existing active orders, then build + place ───────
        for pos in approved:
            # Skip positions whose IBKR order was just cancelled this cycle — the
            # cancel may not have cleared yet and submitting immediately risks a
            # duplicate fill. The position stays APPROVED and retries next cycle.
            if pos["id"] in timed_out_this_cycle:
                logger.debug(
                    "Position %s (%s) just had order cancelled this cycle — deferring entry retry to next cycle",
                    pos["id"], pos.get("ticker"),
                )
                continue

            # E: Skip if an active order already exists (avoids duplicate orders).
            # Also check FILLED entry orders: if a fill was recorded but
            # _close_position_loop failed silently, the position stays stuck in
            # APPROVED while the order is already FILLED. Repair it here so a
            # second order is never placed for the same position.
            existing = (
                client.table("orders")
                .select("id,status,avg_fill_price,exit_type")
                .eq("position_id", pos["id"])
                .in_("status", ["SUBMITTED", "PARTIAL", "FILLED"])
                .execute()
            )
            if existing.data:
                filled_entry = next(
                    (o for o in existing.data
                     if o["status"] == "FILLED" and o.get("exit_type") is None),
                    None,
                )
                if filled_entry and filled_entry.get("avg_fill_price"):
                    fill_price = float(filled_entry["avg_fill_price"])
                    logger.warning(
                        "Position %s (%s) has FILLED entry order but status=APPROVED — "
                        "_close_position_loop failed previously; repairing at $%.4f",
                        pos["id"], pos.get("ticker"), fill_price,
                    )
                    _fill_recorder._close_position_loop(pos["id"], fill_price)
                else:
                    logger.debug(
                        "Position %s (%s) already has an active order (%s) — skipping",
                        pos["id"], pos.get("ticker"),
                        ", ".join(o["status"] for o in existing.data),
                    )
                continue

            # Retry cap: abandon positions that have timed out too many times.
            # A repeated TIMEOUT almost always means the limit price is wrong or
            # the stock has no liquidity at our size — retrying endlessly wastes
            # IBKR order slots and pollutes the orders table.
            timeout_orders_result = (
                client.table("orders")
                .select("id, limit_price, submitted_at")
                .eq("position_id", pos["id"])
                .eq("status", "TIMEOUT")
                .order("submitted_at", desc=True)
                .execute()
            )
            timeout_orders = timeout_orders_result.data or []
            timeout_count = len(timeout_orders)
            if timeout_count >= _MAX_ENTRY_RETRIES:
                client.table("positions").update({"status": "EXPIRED"}).eq("id", pos["id"]).execute()
                logger.warning(
                    "Position %s (%s) exceeded max retries (%d timeouts) — marked EXPIRED",
                    pos["id"], pos.get("ticker"), timeout_count,
                )

                # Re-queue the linked memo for PM re-evaluation with expiry context
                # so the PM can decide whether to retry at current price, resize, or pass.
                memo_id = pos.get("memo_id")
                if memo_id:
                    _requeue_memo_after_expiry(client, memo_id, pos, timeout_orders)

                notify_event("ORDER_ERROR", {
                    "ticker": pos.get("ticker", "—"),
                    "error": f"Abandoned after {timeout_count} timeout(s) — marked EXPIRED, memo re-queued for PM",
                })
                continue

            # Guard: null entry_price causes OrderBuildError every cycle, burning
            # error count forever. Catch it here before the retry cap can trigger.
            if pos.get("entry_price") is None:
                logger.warning(
                    "Position %s (%s) has null entry_price — skipping entry this cycle "
                    "(portfolio agent price fetch likely failed at sizing time)",
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
        all_cycle_order_ids = this_cycle_order_ids + exit_order_ids
        if all_cycle_order_ids:
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
        if this_cycle_order_ids or exit_order_ids:
            _ibkr.disconnect()

    logger.info(
        "Execution cycle complete — approved=%d exits_placed=%d placed=%d filled=%d partial=%d timeout=%d error=%d",
        summary.approved_found, summary.exits_placed, summary.orders_placed, summary.orders_filled,
        summary.orders_partial, summary.orders_timeout, summary.orders_error,
    )
    if summary.orders_placed or summary.exits_placed or summary.orders_filled or summary.orders_error or summary.orders_timeout:
        notify_event("EXECUTION_CYCLE_COMPLETE", {
            "exits_placed":   summary.exits_placed,
            "exits_error":    summary.exits_error,
            "orders_placed":  summary.orders_placed,
            "orders_filled":  summary.orders_filled,
            "orders_partial": summary.orders_partial,
            "orders_timeout": summary.orders_timeout,
            "orders_error":   summary.orders_error,
        })
    return summary
