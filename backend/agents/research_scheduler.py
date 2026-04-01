"""
Research queue poller — fires run_research() for watchlist rows queued after each screen.

Scheduled at 4:30 PM ET Mon–Fri (30 min after the screener cron at 4:00 PM).
Reads watchlist rows where queued_for_research=True for today, calls run_research()
for each in rank order, then clears the flag regardless of individual success/failure.
"""

import asyncio
import logging
from datetime import date
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


async def run_research_job() -> None:
    """Async wrapper — isolates exceptions so the scheduler never crashes."""
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _poll_research_queue)
    except Exception as exc:
        logger.error("Research queue job failed: %s", exc, exc_info=True)


def _poll_research_queue() -> list[str]:
    """Read queued_for_research=True rows for today, call run_research() for each,
    then clear the flag. Returns list of tickers processed."""
    from backend.memory.vector_store import _get_client
    from backend.agents.research_agent import run_research

    try:
        client = _get_client()
    except Exception as exc:
        logger.error("_poll_research_queue: Supabase unavailable — %s", exc)
        return []

    today = date.today().isoformat()
    try:
        result = (
            client.table("watchlist")
            .select("ticker,rank")
            .eq("queued_for_research", True)
            .eq("run_date", today)
            .order("rank")
            .execute()
        )
    except Exception as exc:
        logger.error("_poll_research_queue: watchlist read failed — %s", exc)
        return []

    tickers = [row["ticker"] for row in (result.data or [])]
    if not tickers:
        logger.info("_poll_research_queue: no tickers queued for research today")
        return []

    logger.info("_poll_research_queue: processing %d tickers: %s", len(tickers), tickers)
    processed: list[str] = []
    for ticker in tickers:
        try:
            run_research(ticker, use_cache=False)
            processed.append(ticker)
            logger.info("_poll_research_queue: completed %s", ticker)
        except Exception as exc:
            logger.error("_poll_research_queue: run_research(%s) failed — %s", ticker, exc)

    # Clear the flag for all processed tickers regardless of individual success/failure
    if processed:
        try:
            client.table("watchlist").update({"queued_for_research": False}).in_(
                "ticker", processed
            ).eq("run_date", today).execute()
            logger.info("_poll_research_queue: cleared queued_for_research for %s", processed)
        except Exception as exc:
            logger.warning("_poll_research_queue: failed to clear flag — %s", exc)

    return processed


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
