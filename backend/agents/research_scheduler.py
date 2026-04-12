"""
Research queue poller — fires run_research() for watchlist rows queued after each screen.

Scheduled at 4:30 PM ET Mon–Fri (30 min after the screener cron at 4:00 PM).

Efficiency improvements (2026-04-10):
  - Staleness gate: skips tickers with a memo < 7 days old unless material_event=True
  - Priority ordering: P1 (held+material) → P2 (watchlist+material) → P3 (nightly) → P4 (manual)
  - Daily research cap: hard limit of 10 runs/day, tracked in pm_config; surplus carries to next day
  - update_mode dispatch: held positions without a material event use incremental mode
    (news+transcripts only; skips SEC/Form4/FMP re-fetch and ReAct loop)
"""

import asyncio
import logging
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

load_dotenv()

from backend.notifications.events import notify_event

logger = logging.getLogger(__name__)

DAILY_RESEARCH_CAP = 10
_STALENESS_DAYS = 7


# ── Helpers ───────────────────────────────────────────────────────────────────

def _needs_research(client, ticker: str) -> bool:
    """Return True if this ticker requires a new research run.

    False (skip) when ALL of these hold:
      - A memo exists that is < 7 days old
      - The watchlist entry does NOT have material_event=True
    """
    cutoff = (date.today() - timedelta(days=_STALENESS_DAYS)).isoformat()
    try:
        result = (
            client.table("memos")
            .select("id,date")
            .eq("ticker", ticker)
            .gte("date", cutoff)
            .order("date", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.warning("_needs_research: memos query failed for %s — %s; defaulting to True", ticker, exc)
        return True

    if not result.data:
        return True  # no recent memo → full research

    # Recent memo exists — check for material event flag
    try:
        wl = (
            client.table("watchlist")
            .select("material_event")
            .eq("ticker", ticker)
            .order("run_date", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.warning("_needs_research: watchlist query failed for %s — %s; defaulting to True", ticker, exc)
        return True

    if not wl.data:
        return False  # no watchlist row → conservatively skip

    return bool(wl.data[0].get("material_event", False))


def _is_held_position(client, ticker: str) -> bool:
    """Return True if ticker has an OPEN position."""
    try:
        result = (
            client.table("positions")
            .select("id")
            .eq("ticker", ticker)
            .eq("status", "OPEN")
            .limit(1)
            .execute()
        )
        return bool(result.data)
    except Exception as exc:
        logger.warning("_is_held_position: query failed for %s — %s", ticker, exc)
        return False


def _get_material_event(client, ticker: str) -> bool:
    """Return the material_event flag for the most recent watchlist row."""
    try:
        result = (
            client.table("watchlist")
            .select("material_event")
            .eq("ticker", ticker)
            .order("run_date", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            return bool(result.data[0].get("material_event", False))
    except Exception as exc:
        logger.warning("_get_material_event: query failed for %s — %s", ticker, exc)
    return False


def _get_and_increment_daily_count(client) -> int:
    """Return the daily research run count AFTER incrementing it.

    Resets automatically when the date changes (new calendar day).
    Persisted to pm_config so it survives process restarts.
    """
    today = date.today().isoformat()
    try:
        row = (
            client.table("pm_config")
            .select("daily_research_count,daily_research_date")
            .eq("id", 1)
            .single()
            .execute()
            .data
        )
    except Exception as exc:
        logger.warning("_get_and_increment_daily_count: read failed — %s; assuming 0", exc)
        return 1

    if not row or str(row.get("daily_research_date") or "") != today:
        try:
            client.table("pm_config").update(
                {"daily_research_count": 1, "daily_research_date": today}
            ).eq("id", 1).execute()
        except Exception as exc:
            logger.warning("_get_and_increment_daily_count: reset write failed — %s", exc)
        return 1

    new_count = row["daily_research_count"] + 1
    try:
        client.table("pm_config").update(
            {"daily_research_count": new_count}
        ).eq("id", 1).execute()
    except Exception as exc:
        logger.warning("_get_and_increment_daily_count: increment write failed — %s", exc)
    return new_count


def _clear_material_event(client, ticker: str, today: str) -> None:
    """Clear material_event flag after research completes."""
    try:
        client.table("watchlist").update(
            {"material_event": False, "material_event_reason": None}
        ).eq("ticker", ticker).eq("run_date", today).execute()
    except Exception as exc:
        logger.warning("_clear_material_event: failed for %s — %s", ticker, exc)


# ── Main queue poller ─────────────────────────────────────────────────────────

def _poll_research_queue() -> list[str]:
    """Read queued_for_research=True rows, process in priority order, enforce daily cap.

    Returns list of tickers that were successfully researched.
    Tickers skipped by staleness gate or daily cap retain their queued_for_research flag
    (cap overflows carry to the next day; staleness skips are cleared immediately).
    """
    from backend.memory.vector_store import _get_client
    from backend.agents.research_agent import run_research

    try:
        client = _get_client()
    except Exception as exc:
        logger.error("_poll_research_queue: Supabase unavailable — %s", exc)
        return []

    today = date.today().isoformat()

    # Fetch queue sorted by priority (P1 first) then composite rank
    try:
        result = (
            client.table("watchlist")
            .select("ticker,rank,priority,material_event")
            .eq("queued_for_research", True)
            .eq("run_date", today)
            .order("priority", desc=False)
            .order("rank", desc=False)
            .execute()
        )
    except Exception as exc:
        logger.error("_poll_research_queue: watchlist read failed — %s", exc)
        return []

    rows = result.data or []
    if not rows:
        logger.info("_poll_research_queue: no tickers queued for research today")
        return []

    import asyncio as _asyncio
    import os as _os
    from backend.agents.portfolio_agent import run_portfolio_sizing, PortfolioAgentError
    from backend.memory.vector_store import store_memo

    from backend.broker.ibkr import get_portfolio_value as _get_portfolio_value
    portfolio_value = _get_portfolio_value()
    tickers = [row["ticker"] for row in rows]
    logger.info(
        "_poll_research_queue: %d tickers in queue (priority order): %s",
        len(tickers), tickers,
    )

    processed: list[str] = []
    staleness_skipped: list[str] = []

    for row in rows:
        ticker = row["ticker"]

        # ── Daily cap check ───────────────────────────────────────────────────
        count = _get_and_increment_daily_count(client)
        if count > DAILY_RESEARCH_CAP:
            logger.warning(
                "_poll_research_queue: daily cap (%d) hit — %s and remaining tickers "
                "carry to next day",
                DAILY_RESEARCH_CAP, ticker,
            )
            break  # remaining rows keep queued_for_research=True

        # ── Staleness gate ────────────────────────────────────────────────────
        if not _needs_research(client, ticker):
            logger.info(
                "staleness gate: skipping %s — memo < %d days old, no material event",
                ticker, _STALENESS_DAYS,
            )
            staleness_skipped.append(ticker)
            continue

        # ── Determine update_mode ─────────────────────────────────────────────
        is_held = _is_held_position(client, ticker)
        has_material_event = bool(row.get("material_event", False))
        # Incremental update: held positions with no material event use news+transcripts only
        update_mode = is_held and not has_material_event

        if update_mode:
            logger.info("_poll_research_queue: %s is held, no material event — using update_mode", ticker)
        else:
            logger.info(
                "_poll_research_queue: %s — full research (held=%s, material_event=%s)",
                ticker, is_held, has_material_event,
            )

        # ── Run research ──────────────────────────────────────────────────────
        try:
            memo = run_research(ticker, use_cache=False, update_mode=update_mode)
            processed.append(ticker)
            logger.info("_poll_research_queue: completed %s", ticker)
        except Exception as exc:
            logger.error("_poll_research_queue: run_research(%s) failed — %s", ticker, exc)
            continue

        # Clear material event flag after successful research
        _clear_material_event(client, ticker, today)

        # ── Portfolio sizing ──────────────────────────────────────────────────
        try:
            memo_id = store_memo(ticker, memo)
            if memo_id:
                notify_event("RESEARCH_MEMO_COMPLETED", {
                    "ticker": ticker,
                    "verdict": memo.get("verdict"),
                    "conviction_score": memo.get("conviction_score"),
                    "sector": memo.get("sector"),
                    "price_target": memo.get("price_target"),
                })
                _asyncio.run(run_portfolio_sizing(memo_id=memo_id, portfolio_value=portfolio_value))
                logger.info("_poll_research_queue: portfolio sizing complete for %s", ticker)
            else:
                logger.warning(
                    "_poll_research_queue: store_memo returned no id for %s — skipping sizing", ticker
                )
        except PortfolioAgentError as exc:
            logger.warning("_poll_research_queue: portfolio sizing skipped for %s — %s", ticker, exc)
        except Exception as exc:
            logger.error("_poll_research_queue: portfolio sizing failed for %s — %s", ticker, exc)

    # Clear queued_for_research for processed + staleness-skipped tickers
    to_clear = processed + staleness_skipped
    if to_clear:
        try:
            client.table("watchlist").update({"queued_for_research": False}).in_(
                "ticker", to_clear
            ).eq("run_date", today).execute()
            logger.info("_poll_research_queue: cleared queued_for_research for %s", to_clear)
        except Exception as exc:
            logger.warning("_poll_research_queue: failed to clear flag — %s", exc)

    return processed


async def run_research_job() -> None:
    """Async wrapper — isolates exceptions so the scheduler never crashes."""
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _poll_research_queue)
    except Exception as exc:
        logger.error("Research queue job failed: %s", exc, exc_info=True)


def create_research_scheduler() -> AsyncIOScheduler:
    """Return a configured (not yet started) research queue poller.
    Fires at 4:30 PM ET Mon–Fri — 30 min after the screener cron at 4:00 PM."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_research_job,
        trigger=CronTrigger(
            hour=16,
            minute=30,
            day_of_week="mon-fri",
            timezone=ZoneInfo("America/New_York"),
        ),
        id="research_queue_daily",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    return scheduler
