"""
Macro Scheduler — triggers the full macro pipeline daily at 7AM ET.

Register via create_macro_scheduler() in backend/main.py alongside
the screener scheduler.

Pattern mirrors backend/screener/scheduler.py.
"""

from dotenv import load_dotenv

load_dotenv()

import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


async def run_macro_job() -> None:
    """
    Async wrapper for the macro pipeline. Logs start/end including regime and confidence.
    Never propagates exceptions — failures are logged but do not crash the scheduler.
    """
    from backend.agents.macro_agent import run_macro_pipeline, MacroAgentError

    logger.info("Macro job starting (7AM ET trigger)")
    try:
        briefing = run_macro_pipeline()
        logger.info(
            "Macro job complete — regime=%s confidence=%.1f",
            briefing.regime,
            briefing.regime_confidence,
        )
    except MacroAgentError as exc:
        logger.error("Macro pipeline error: %s", exc)
    except Exception as exc:
        logger.exception("Unexpected macro job failure: %s", exc)


def create_macro_scheduler() -> AsyncIOScheduler:
    """
    Create and configure the APScheduler for daily 7AM ET macro analysis.

    Returns an AsyncIOScheduler (not yet started).
    Call scheduler.start() in the FastAPI lifespan event.
    """
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_macro_job,
        trigger=CronTrigger(
            hour=7,
            minute=0,
            day_of_week="mon-fri",
            timezone=ZoneInfo("America/New_York"),
        ),
        id="macro_daily",
        replace_existing=True,
        misfire_grace_time=3600,  # run within 1 hour if missed (e.g. server restart)
    )
    logger.info("Macro scheduler configured — daily at 07:00 ET (Mon–Fri)")
    return scheduler
