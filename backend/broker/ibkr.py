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