"""
Fill Recorder — IBKR execDetailsEvent handler and position loop closer.

Wired into the execution cycle via:
    ib.execDetailsEvent += handle_exec_detail

Three public entry points:
    handle_exec_detail(trade, fill) -> None
        Registered as an ib_insync event callback. Inserts a fills row,
        recomputes the VWAP aggregate on the orders row, and transitions
        the position to OPEN when fully filled.

    record_partial_fill_open(order_id) -> None
        Called by execution_agent when a VWAP order times out with a
        partial fill > 0. Opens the position at the partial quantity.

Private helpers:
    _update_order_aggregate(order_id, order_row, new_fill_qty) -> None
        Recomputes total_filled_qty and avg_fill_price (VWAP) across all
        fills for the order, updates the orders row, and triggers
        _close_position_loop when the order is fully filled.

    _close_position_loop(position_id, avg_fill_price) -> None
        Writes OPEN status + entry_price to the positions row.

    _parse_ibkr_time(raw) -> str
        Best-effort parser for ib_insync fill timestamp strings.
"""

from dotenv import load_dotenv

load_dotenv()

import logging
from datetime import datetime
from typing import Optional

from backend.memory.vector_store import _get_client
from backend.notifications.events import notify_event
from backend.broker.ibkr import get_cash_balance

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Public: IBKR event callback
# ──────────────────────────────────────────────────────────────────────────────


def handle_exec_detail(trade, fill) -> None:
    """
    Handle a single ib_insync execDetailsEvent callback.

    Inserts one row into the fills table for this partial or full fill,
    then recomputes the VWAP aggregate on the parent orders row. When the
    order reaches fully-filled status, transitions the position from
    APPROVED to OPEN via _close_position_loop.

    Never raises — IBKR event callbacks must not propagate exceptions.

    Args:
        trade: ib_insync Trade object; trade.order.permId identifies the order.
        fill:  ib_insync Fill object; carries contract, execution, and
               commissionReport attributes.
    """
    try:
        perm_id = trade.order.permId
        if not perm_id:
            logger.warning("Received fill with no permId — skipping")
            return

        client = _get_client()

        # 1. Look up the orders row by ibkr_order_id (permId).
        result = (
            client.table("orders")
            .select("*")
            .eq("ibkr_order_id", perm_id)
            .execute()
        )
        if not result.data:
            logger.warning(
                "No orders row found for permId=%s — possibly from a prior session",
                perm_id,
            )
            return

        order_row = result.data[0]
        order_id: str = order_row["id"]
        position_id: str = order_row["position_id"]
        intended_price: float = float(order_row.get("limit_price") or 0)

        # 2. Parse fill details from the ib_insync Fill object.
        fill_qty: float = float(fill.execution.shares)
        fill_price: float = float(fill.execution.price)

        commission: Optional[float] = None
        if fill.commissionReport and fill.commissionReport.commission:
            try:
                commission = float(fill.commissionReport.commission)
            except (TypeError, ValueError):
                commission = None

        exchange: Optional[str] = fill.execution.exchange or None

        # Parse fill_time — ib_insync may return a datetime or a string such as
        # "20260401 14:35:22 ET" or "20260401  14:35:22". Fall back to UTC now
        # when the format is unrecognised so fills are never dropped on parse errors.
        raw_time = fill.execution.time
        if isinstance(raw_time, datetime):
            fill_time_iso: str = raw_time.isoformat()
        else:
            fill_time_iso = _parse_ibkr_time(str(raw_time))

        # 3. Compute slippage in basis points (positive = filled above intended).
        slippage_bps: Optional[float] = None
        if intended_price and intended_price > 0:
            slippage_bps = round(
                (fill_price - intended_price) / intended_price * 10000, 2
            )

        # 4. Insert a row into fills.
        client.table("fills").insert(
            {
                "order_id": order_id,
                "position_id": position_id,
                "ticker": order_row["ticker"],
                "fill_qty": fill_qty,
                "fill_price": fill_price,
                "fill_time": fill_time_iso,
                "commission": commission,
                "exchange": exchange,
                "slippage_bps": slippage_bps,
                "intended_price": intended_price if intended_price > 0 else None,
            }
        ).execute()

        # 5. Recompute the VWAP aggregate on the parent orders row.
        _update_order_aggregate(order_id, order_row, fill_qty)

    except Exception as exc:
        perm_id_safe = None
        try:
            perm_id_safe = trade.order.permId
        except Exception:
            pass
        logger.error("fill handler error for permId=%s: %s", perm_id_safe, exc)


# ──────────────────────────────────────────────────────────────────────────────
# Public: partial fill opener (called by execution_agent on VWAP timeout)
# ──────────────────────────────────────────────────────────────────────────────


def record_partial_fill_open(order_id: str) -> None:
    """
    Open a position at partial quantity when a VWAP order times out.

    Called by execution_agent when check_timeouts() returns a position_id
    whose order has total_filled_qty > 0. Policy: accept the partial fill,
    size the open position to the actual filled quantity, and annotate the
    sizing_rationale to preserve audit history.

    Never raises — logs errors to stderr on any failure.

    Args:
        order_id: UUID from the orders table.
    """
    try:
        client = _get_client()

        # Fetch the order row.
        result = client.table("orders").select("*").eq("id", order_id).execute()
        if not result.data:
            logger.warning(
                "record_partial_fill_open: no order row found for order_id=%s",
                order_id,
            )
            return

        order_row = result.data[0]
        total_filled: float = float(order_row.get("total_filled_qty") or 0)
        avg_fill_price: float = float(order_row.get("avg_fill_price") or 0)

        if total_filled <= 0 or avg_fill_price <= 0:
            logger.info(
                "Order %s has no fills — not opening position", order_id
            )
            return

        position_id: str = order_row["position_id"]
        dollar_size: float = round(total_filled * avg_fill_price, 2)

        # Transition the position to OPEN at the partial fill price and qty.
        client.table("positions").update(
            {
                "status": "OPEN",
                "entry_price": round(avg_fill_price, 4),
                "current_price": round(avg_fill_price, 4),  # at open, current == entry
                "pnl": 0.0,
                "pnl_pct": 0.0,
                "share_count": total_filled,
                "dollar_size": dollar_size,
                "opened_at": datetime.utcnow().isoformat(),
            }
        ).eq("id", position_id).execute()

        # Append a note to sizing_rationale so the partial fill is visible in
        # the portfolio UI without overwriting the original rationale text.
        pos_result = (
            client.table("positions")
            .select("sizing_rationale")
            .eq("id", position_id)
            .execute()
        )
        if pos_result.data:
            existing: str = pos_result.data[0].get("sizing_rationale") or ""
            requested: float = float(order_row.get("requested_qty") or 0)
            note = (
                f" [PARTIAL FILL: {total_filled:.0f}/{requested:.0f} shares"
                f" at ${avg_fill_price:.4f}]"
            )
            client.table("positions").update(
                {"sizing_rationale": existing + note}
            ).eq("id", position_id).execute()

        logger.info(
            "Position %s OPEN (partial): %.0f shares at $%.4f",
            position_id,
            total_filled,
            avg_fill_price,
        )
        _sync_cash_and_pct(client)
        notify_event("ORDER_FILLED", {
            "ticker": order_row.get("ticker", "—"),
            "fill_qty": total_filled,
            "fill_price": round(avg_fill_price, 4),
            "fill_type": "PARTIAL",
        })

    except Exception as exc:
        logger.error(
            "record_partial_fill_open failed for order_id=%s: %s", order_id, exc
        )


# ──────────────────────────────────────────────────────────────────────────────
# Private helpers
# ──────────────────────────────────────────────────────────────────────────────


def _update_order_aggregate(
    order_id: str, order_row: dict, new_fill_qty: float
) -> None:
    """
    Recompute the VWAP aggregate for an order after a new fill arrives.

    Queries all fills rows for order_id, computes total_filled_qty and
    avg_fill_price (true VWAP), then writes them back to the orders row.
    If the order is now fully filled, also sets status=FILLED and
    filled_at, and calls _close_position_loop to open the position.

    Never raises — errors are logged to stderr.

    Args:
        order_id:     UUID of the orders row to update.
        order_row:    The dict previously fetched for this order (used for
                      requested_qty and position_id without an extra query).
        new_fill_qty: Quantity from the fill that just arrived (informational;
                      VWAP is computed from the fills table, not incremental).
    """
    try:
        client = _get_client()

        # Re-query all fills for this order to compute a true VWAP.
        fills_result = (
            client.table("fills")
            .select("fill_qty,fill_price")
            .eq("order_id", order_id)
            .execute()
        )
        all_fills = fills_result.data or []

        total_qty: float = sum(float(f["fill_qty"]) for f in all_fills)
        avg_price: Optional[float] = None
        if total_qty > 0:
            avg_price = (
                sum(float(f["fill_qty"]) * float(f["fill_price"]) for f in all_fills)
                / total_qty
            )

        # Determine whether the order is now fully filled.
        requested_qty: float = float(order_row.get("requested_qty") or 0)
        is_filled: bool = requested_qty > 0 and total_qty >= requested_qty

        update_data: dict = {
            "total_filled_qty": total_qty,
            "avg_fill_price": round(avg_price, 4) if avg_price is not None else None,
            "status": "FILLED" if is_filled else "PARTIAL",
        }
        if is_filled:
            update_data["filled_at"] = datetime.utcnow().isoformat()

        client.table("orders").update(update_data).eq("id", order_id).execute()

        logger.info(
            "Order %s aggregate updated: total_qty=%.0f, avg_price=%s, status=%s",
            order_id,
            total_qty,
            f"${avg_price:.4f}" if avg_price is not None else "None",
            update_data["status"],
        )

        # Close the position loop once the order is fully filled.
        if is_filled and avg_price is not None:
            _close_position_loop(order_row["position_id"], avg_price)
            _sync_cash_and_pct(client)
            notify_event("ORDER_FILLED", {
                "ticker": order_row.get("ticker", "—"),
                "fill_qty": total_qty,
                "fill_price": round(avg_price, 4),
                "fill_type": "FULL",
                "slippage_bps": round(
                    (avg_price - float(order_row.get("limit_price") or avg_price))
                    / float(order_row.get("limit_price") or avg_price) * 10000, 2
                ) if order_row.get("limit_price") else None,
            })

    except Exception as exc:
        logger.error(
            "_update_order_aggregate failed for order_id=%s: %s", order_id, exc
        )


def _close_position_loop(position_id: str, avg_fill_price: float) -> None:
    """
    Transition a position from APPROVED to OPEN after full order fill.

    Writes entry_price and opened_at to the positions row. This is the
    final step that closes the APPROVED -> OPEN loop and makes the position
    visible to the risk monitor and portfolio metrics engine.

    Never raises — errors are logged to stderr.

    Args:
        position_id:    UUID of the positions row to update.
        avg_fill_price: VWAP computed from all fills for the completed order.
    """
    try:
        client = _get_client()
        client.table("positions").update(
            {
                "status": "OPEN",
                "entry_price": round(avg_fill_price, 4),
                "current_price": round(avg_fill_price, 4),  # at open, current == entry
                "pnl": 0.0,
                "pnl_pct": 0.0,
                "opened_at": datetime.utcnow().isoformat(),
            }
        ).eq("id", position_id).execute()
        logger.info(
            "Position %s OPEN at avg fill price $%.4f",
            position_id,
            avg_fill_price,
        )
    except Exception as exc:
        logger.error(
            "Failed to close position loop for position_id=%s: %s",
            position_id,
            exc,
        )


def _sync_cash_and_pct(client) -> None:
    """
    Sync IBKR cash balance to pm_config and recalculate pct_of_portfolio for
    all OPEN positions.

    Called immediately after every full or partial position open so the
    dashboard and PM agent always see an accurate portfolio composition.

    Steps:
      1. Fetch USD CashBalance from IBKR. Skip silently if IBKR is unreachable.
      2. Update pm_config (id=1): cash_balance = <ibkr_cash>, updated_at = NOW().
      3. Load all OPEN positions (id, dollar_size).
      4. Compute total_portfolio_value = cash + sum(dollar_size).
      5. Write pct_of_portfolio = dollar_size / total_portfolio_value * 100
         for every open position.

    Never raises — errors are logged to stderr.

    Args:
        client: Supabase client from _get_client() (already in scope at call site).
    """
    try:
        cash = get_cash_balance()
        if cash is None:
            logger.warning("_sync_cash_and_pct: IBKR unreachable — skipping sync")
            return

        # 1. Persist cash balance.
        client.table("pm_config").update(
            {"cash_balance": round(cash, 2), "updated_at": datetime.utcnow().isoformat()}
        ).eq("id", 1).execute()
        logger.info("pm_config cash_balance synced: $%.2f", cash)

        # 2. Load all OPEN positions.
        pos_result = (
            client.table("positions")
            .select("id,dollar_size")
            .eq("status", "OPEN")
            .execute()
        )
        open_positions = pos_result.data or []
        if not open_positions:
            return

        market_value: float = sum(
            float(p.get("dollar_size") or 0) for p in open_positions
        )
        total_value: float = cash + market_value
        if total_value <= 0:
            logger.warning("_sync_cash_and_pct: total_value=%.2f — skipping pct update", total_value)
            return

        # 3. Update each open position's pct_of_portfolio.
        for pos in open_positions:
            dollar_size = float(pos.get("dollar_size") or 0)
            pct = round(dollar_size / total_value * 100, 4)
            client.table("positions").update(
                {"pct_of_portfolio": pct}
            ).eq("id", pos["id"]).execute()

        logger.info(
            "pct_of_portfolio refreshed for %d OPEN positions "
            "(total_value=$%.2f, cash=$%.2f, market=$%.2f)",
            len(open_positions), total_value, cash, market_value,
        )

    except Exception as exc:
        logger.error("_sync_cash_and_pct failed: %s", exc)


def _parse_ibkr_time(raw: str) -> str:
    """
    Best-effort parser for ib_insync fill timestamp strings.

    ib_insync typically returns one of:
        "20260401 14:35:22 ET"
        "20260401  14:35:22"
        "20260401 14:35:22"

    Returns an ISO-format string. Falls back to datetime.utcnow().isoformat()
    when the string cannot be parsed so fills are never dropped due to a
    timestamp format edge case.

    Args:
        raw: Raw timestamp string from fill.execution.time.

    Returns:
        ISO-format timestamp string.
    """
    cleaned = raw.strip()
    # Split on whitespace and discard trailing timezone labels (e.g. "ET").
    non_empty = [p for p in cleaned.split() if p]
    if len(non_empty) >= 2:
        date_str = non_empty[0]   # e.g. "20260401"
        time_str = non_empty[1]   # e.g. "14:35:22"
        try:
            dt = datetime.strptime(f"{date_str} {time_str}", "%Y%m%d %H:%M:%S")
            return dt.isoformat()
        except ValueError:
            pass

    # Final fallback — log so the data team can investigate unusual formats.
    logger.warning(
        "Could not parse IBKR fill timestamp '%s' — using UTC now", raw
    )
    return datetime.utcnow().isoformat()
