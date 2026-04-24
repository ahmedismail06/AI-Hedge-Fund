"""
IBKR Connection Manager.

Runs ib_insync in a dedicated background thread with its own event loop,
isolated from FastAPI's asyncio loop. This prevents the "Future attached to
a different loop" error that occurs when ib_insync tries to create tasks
inside FastAPI's running event loop.
"""

import asyncio
import logging
import os
import threading
import time
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from ib_insync import IB, util

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Module-level singleton state
# ──────────────────────────────────────────────────────────────────────────────

_ib: Optional[IB] = None
_ib_loop: Optional[asyncio.AbstractEventLoop] = None
_ib_thread: Optional[threading.Thread] = None
_lock = threading.Lock()


class IBKRConnectionError(Exception):
    pass


def is_paper() -> bool:
    return os.getenv("ENV", "paper").lower() != "live"


# ──────────────────────────────────────────────────────────────────────────────
# Background thread — owns the ib_insync event loop
# ──────────────────────────────────────────────────────────────────────────────

def _start_ib_thread() -> None:
    """Start a dedicated background thread running its own event loop for ib_insync."""
    global _ib_loop, _ib_thread

    if _ib_thread is not None and _ib_thread.is_alive():
        return

    _ib_loop = asyncio.new_event_loop()

    def run_loop():
        asyncio.set_event_loop(_ib_loop)
        _ib_loop.run_forever()

    _ib_thread = threading.Thread(target=run_loop, daemon=True, name="ibkr-event-loop")
    _ib_thread.start()
    logger.info("IBKR background event loop thread started")


# ──────────────────────────────────────────────────────────────────────────────
# Core connection logic
# ──────────────────────────────────────────────────────────────────────────────

def _get_ib() -> IB:
    global _ib

    if _ib is not None and _ib.isConnected():
        return _ib

    _start_ib_thread()

    host = os.getenv("IBKR_HOST", "127.0.0.1")
    port = 7497 if is_paper() else 7496
    client_id = int(os.getenv("IBKR_CLIENT_ID", "1"))

    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            # Run the connection coroutine on the dedicated ib_insync loop
            future = asyncio.run_coroutine_threadsafe(
                _connect_async(host, port, client_id),
                _ib_loop
            )
            future.result(timeout=15)  # block until connected or timeout
            logger.info("Connected to IBKR at %s:%d (paper=%s)", host, port, is_paper())
            return _ib
        except Exception as exc:
            last_exc = exc
            sleep_seconds = 2 ** attempt
            logger.warning(
                "IBKR connection attempt %d/3 failed: %s — retrying in %ds",
                attempt + 1, exc, sleep_seconds,
            )
            time.sleep(sleep_seconds)

    raise IBKRConnectionError(
        f"Failed to connect to IBKR at {host}:{port} after 3 attempts"
    ) from last_exc


async def _connect_async(host: str, port: int, client_id: int) -> None:
    """Coroutine that runs on the dedicated ib_insync loop."""
    global _ib
    ib = IB()
    await ib.connectAsync(host, port, clientId=client_id, timeout=10)
    _ib = ib


def connect() -> IB:
    with _lock:
        return _get_ib()


def disconnect() -> None:
    global _ib
    try:
        if _ib is not None and _ib.isConnected():
            future = asyncio.run_coroutine_threadsafe(
                _disconnect_async(), _ib_loop
            )
            future.result(timeout=5)
            logger.info("Disconnected from IBKR")
    except Exception as exc:
        logger.warning("Error during IBKR disconnect (ignored): %s", exc)
    finally:
        _ib = None

async def _disconnect_async() -> None:
    global _ib
    if _ib is not None:
        _ib.disconnect()
        _ib = None


def get_loop() -> asyncio.AbstractEventLoop:
    """Return the dedicated ib_insync event loop, starting the background thread if needed."""
    _start_ib_thread()
    return _ib_loop


# ──────────────────────────────────────────────────────────────────────────────
# Account summary
# ──────────────────────────────────────────────────────────────────────────────

_ACCOUNT_TAGS_WANTED = {'NetLiquidation', 'TotalCashValue', 'CashBalance', 'UnrealizedPnL', 'RealizedPnL'}


def save_account_snapshot(source: str, summary: Optional[dict] = None) -> None:
    """
    Persist a point-in-time IBKR account state to the account_snapshots table.

    Called after every order placed, fill received, or order cancelled so the
    table always reflects the latest known portfolio NAV. Also called
    opportunistically whenever get_portfolio_value() succeeds — keeping the
    snapshot fresh even during idle periods with no trades.

    Never raises — a failed write must not abort order placement or fill handling.

    Args:
        source:  Label for what triggered the snapshot ('post_order', 'post_fill',
                 'post_cancel', 'post_partial_fill', 'opportunistic').
        summary: Pre-fetched get_account_summary() dict.  If omitted the function
                 fetches one itself to avoid a second IBKR round-trip at call sites
                 that already have the dict in hand.
    """
    try:
        if summary is None:
            summary = get_account_summary()
        if not summary:
            return
        nav = summary.get("NetLiquidation")
        if not nav or nav <= 0:
            return
        from backend.memory.vector_store import _get_client  # lazy — avoids circular import
        _get_client().table("account_snapshots").insert(
            {
                "net_liquidation": round(float(nav), 2),
                "cash": round(float(summary.get("CashBalance") or 0), 2),
                "total_cash_value": round(float(summary.get("TotalCashValue") or 0), 2),
                "unrealized_pnl": round(float(summary.get("UnrealizedPnL") or 0), 2),
                "realized_pnl": round(float(summary.get("RealizedPnL") or 0), 2),
                "source": source,
            }
        ).execute()
        logger.debug("account_snapshot saved: net_liq=%.2f source=%s", nav, source)
    except Exception as exc:
        logger.warning("save_account_snapshot failed (%s): %s", source, exc)


def get_portfolio_value() -> float:
    """
    Return the live portfolio NAV (NetLiquidation).

    Resolution order:
      1. IBKR NetLiquidation (live, if connected and > 0).  Also writes an
         opportunistic account_snapshot so the fallback stays fresh.
      2. Most recent account_snapshots row (if IBKR is unreachable).
      3. Raises RuntimeError — callers must handle IBKR-down + cold-start.

    This is the single source of truth for portfolio_value across all agents
    and API endpoints.  Never hardcode a NAV or read from env — call this.
    """
    summary = get_account_summary()
    nav = summary.get("NetLiquidation")
    if nav and nav > 0:
        logger.debug("portfolio_value from IBKR NetLiquidation: %.2f", nav)
        save_account_snapshot("opportunistic", summary=summary)
        return float(nav)

    logger.warning("IBKR unreachable — falling back to account_snapshots")
    try:
        from backend.memory.vector_store import _get_client  # lazy — avoids circular import
        result = (
            _get_client()
            .table("account_snapshots")
            .select("net_liquidation,captured_at")
            .gt("net_liquidation", 0)
            .order("captured_at", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            snap_nav = float(result.data[0]["net_liquidation"])
            if snap_nav > 0:
                logger.warning(
                    "portfolio_value from account_snapshot (IBKR offline): %.2f "
                    "(captured_at=%s)",
                    snap_nav,
                    result.data[0].get("captured_at"),
                )
                return snap_nav
    except Exception as exc:
        logger.error("account_snapshot fallback query failed: %s", exc)

    raise RuntimeError(
        "No portfolio value available: IBKR is unreachable and no account_snapshot "
        "has ever been recorded.  Ensure TWS/IB Gateway is running so the first "
        "snapshot can be captured, or verify Supabase connectivity."
    )


def get_account_summary() -> dict:
    """
    Return key IBKR account values from the cached accountValues() list.
    ib_insync keeps this cache updated automatically once connected.
    Auto-reconnects silently if the connection was dropped (e.g. after an
    execution cycle). Returns empty dict only if reconnect also fails.
    """
    global _ib
    with _lock:
        if _ib is None or not _ib.isConnected():
            try:
                _get_ib()  # attempt reconnect
            except Exception:
                return {}
    try:
        result = {}
        for av in _ib.accountValues():
            if av.tag in _ACCOUNT_TAGS_WANTED and av.currency == 'USD':
                try:
                    result[av.tag] = float(av.value)
                except (ValueError, TypeError):
                    pass
        return result
    except Exception as exc:
        logger.warning("Failed to fetch account summary: %s", exc)
        return {}


def get_cash_balance() -> Optional[float]:
    """Return the live USD cash balance from IBKR. Returns None when unreachable."""
    val = get_account_summary().get("CashBalance")
    return float(val) if val is not None else None