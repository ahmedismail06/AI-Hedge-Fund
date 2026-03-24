"""
Screener Scheduler — triggers the full screening pipeline daily at 4PM ET.

Register via create_screener_scheduler() in backend/main.py alongside
the macro scheduler.

Pattern mirrors backend/macro/scheduler.py.
"""

import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


async def run_screening_job() -> None:
    """
    Async wrapper for the screening pipeline. Logs start/end.
    Never propagates exceptions — failures are logged but do not crash the scheduler.
    """
    from backend.agents.screening_agent import run_screening, ScreeningAgentError

    logger.info("Screener job starting (4PM ET trigger)")
    try:
        results = run_screening()
        logger.info("Screener job complete — %d qualified tickers", len(results))
    except ScreeningAgentError as exc:
        logger.error("Screener pipeline error: %s", exc)
    except Exception as exc:
        logger.exception("Unexpected screener job failure: %s", exc)


def create_screener_scheduler() -> AsyncIOScheduler:
    """
    Create and configure the APScheduler for daily 4PM ET screening.

    Returns an AsyncIOScheduler (not yet started).
    Call scheduler.start() in the FastAPI lifespan event.
    """
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_screening_job,
        trigger=CronTrigger(
            hour=16,
            minute=0,
            day_of_week="mon-fri",
            timezone=ZoneInfo("America/New_York"),
        ),
        id="screener_daily",
        replace_existing=True,
        misfire_grace_time=3600,  # run within 1 hour if missed (e.g. server restart)
    )
    logger.info("Screener scheduler configured — daily at 16:00 ET (Mon–Fri)")
    return scheduler
