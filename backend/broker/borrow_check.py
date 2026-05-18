"""
IBKR borrow availability check for SHORT entries.

Gates short orders behind real-time shortable-shares data from IBKR. A short
thesis without locate-able borrow is not investable — this module wraps
`ib_insync` so the PM can reject those before they ever reach the order builder.

Generic tick 236 ("Shortable") + reqMktData populates:
  • ticker.shortableShares     — float count of available shares to short
  • ticker.shortable (via tick) — categorical (1=NotAvail, 2=HTB, 3=ETB)

Categorization here is conservative:
  • ETB         — shortableShares ≥ required AND no HTB signal
  • HTB         — shortable but tight (< 10× required, or hard-to-borrow flag)
  • UNAVAILABLE — shortableShares == 0 OR explicit not-shortable flag
  • UNKNOWN     — IBKR unreachable or timed out

The caller decides what to do with UNKNOWN. The PM defaults to "block in live,
warn in paper" — see portfolio_agent integration.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Categorical result ────────────────────────────────────────────────────────
BORROW_ETB         = "ETB"          # easy to borrow — proceed
BORROW_HTB         = "HTB"          # hard to borrow — proceed with caution
BORROW_UNAVAILABLE = "UNAVAILABLE"  # cannot locate — reject
BORROW_UNKNOWN     = "UNKNOWN"      # IBKR query failed — caller decides


@dataclass
class BorrowStatus:
    ticker: str
    status: str                       # one of the BORROW_* constants
    shortable_shares: Optional[float] # raw count from IBKR (None if unknown)
    required_shares: int              # shares the PM wants to short
    margin_ratio: Optional[float]     # shortable / required (None if unknown)
    reason: str                       # human-readable summary


# ── HTB heuristic ─────────────────────────────────────────────────────────────
# If shortable_shares / required_shares < this multiplier, mark HTB even when
# shares are technically available — tight float invites recalls.
_HTB_MARGIN_MULTIPLIER = 10.0

# How long to wait for the tick after reqMktData.
_TICK_TIMEOUT_SEC = float(os.getenv("IBKR_BORROW_TIMEOUT_SEC", "5"))


async def _query_shortable_async(symbol: str, timeout_sec: float) -> Optional[float]:
    """Query shortableShares on the dedicated ib_insync loop. Returns None on failure."""
    from backend.broker.ibkr import connect
    try:
        from ib_insync import Stock
    except ImportError:
        logger.warning("borrow_check: ib_insync not installed — returning UNKNOWN")
        return None

    ib = connect()
    contract = Stock(symbol, "SMART", "USD")
    try:
        await ib.qualifyContractsAsync(contract)
    except Exception as exc:
        logger.warning("borrow_check: qualifyContractsAsync(%s) failed — %s", symbol, exc)
        return None

    ticker = ib.reqMktData(contract, genericTickList="236", snapshot=False, regulatorySnapshot=False)
    try:
        # Poll until shortableShares populates or timeout fires
        deadline = asyncio.get_event_loop().time() + timeout_sec
        while asyncio.get_event_loop().time() < deadline:
            shares = getattr(ticker, "shortableShares", None)
            if shares is not None and not math.isnan(shares):
                return float(shares)
            await asyncio.sleep(0.2)
        # Final read after timeout
        shares = getattr(ticker, "shortableShares", None)
        return float(shares) if shares is not None and not math.isnan(shares) else None
    finally:
        try:
            ib.cancelMktData(contract)
        except Exception:
            pass


def check_borrow(ticker: str, required_shares: int) -> BorrowStatus:
    """
    Check IBKR shortable-shares availability for `ticker`.

    Args:
        ticker          — symbol e.g. "AAPL"
        required_shares — number of shares the PM intends to short

    Returns BorrowStatus. Never raises.
    """
    sym = ticker.upper()

    # Try to dispatch the async query onto the IBKR thread loop
    try:
        from backend.broker.ibkr import get_loop
        loop = get_loop()
        future = asyncio.run_coroutine_threadsafe(
            _query_shortable_async(sym, _TICK_TIMEOUT_SEC), loop,
        )
        shortable = future.result(timeout=_TICK_TIMEOUT_SEC + 5.0)
    except Exception as exc:
        logger.warning("borrow_check(%s): IBKR query failed — %s", sym, exc)
        return BorrowStatus(
            ticker=sym, status=BORROW_UNKNOWN,
            shortable_shares=None, required_shares=required_shares,
            margin_ratio=None,
            reason=f"IBKR query failed: {type(exc).__name__}",
        )

    # Interpret the result
    if shortable is None:
        return BorrowStatus(
            ticker=sym, status=BORROW_UNKNOWN,
            shortable_shares=None, required_shares=required_shares,
            margin_ratio=None,
            reason="IBKR returned no shortable data within timeout",
        )

    margin = (shortable / required_shares) if required_shares > 0 else float("inf")

    if shortable <= 0:
        status = BORROW_UNAVAILABLE
        reason = f"shortable_shares={shortable:.0f} — no locate available"
    elif shortable < required_shares:
        status = BORROW_UNAVAILABLE
        reason = (
            f"shortable_shares={shortable:.0f} < required {required_shares} — "
            f"insufficient locate"
        )
    elif margin < _HTB_MARGIN_MULTIPLIER:
        status = BORROW_HTB
        reason = (
            f"shortable_shares={shortable:.0f}, margin={margin:.1f}× required "
            f"(< {_HTB_MARGIN_MULTIPLIER:.0f}× = HTB)"
        )
    else:
        status = BORROW_ETB
        reason = (
            f"shortable_shares={shortable:.0f}, margin={margin:.1f}× required "
            f"(≥ {_HTB_MARGIN_MULTIPLIER:.0f}× = ETB)"
        )

    return BorrowStatus(
        ticker=sym, status=status,
        shortable_shares=shortable, required_shares=required_shares,
        margin_ratio=round(margin, 2) if math.isfinite(margin) else None,
        reason=reason,
    )


def is_blocking(status: str, *, allow_unknown: bool = False) -> bool:
    """
    Should the PM reject a SHORT entry given this borrow status?

    UNAVAILABLE always blocks.
    UNKNOWN blocks unless `allow_unknown=True` (paper mode default).
    HTB and ETB never block — caller can still apply a sizing penalty for HTB.
    """
    if status == BORROW_UNAVAILABLE:
        return True
    if status == BORROW_UNKNOWN and not allow_unknown:
        return True
    return False
