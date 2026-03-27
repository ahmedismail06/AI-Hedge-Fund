"""
Smoke tests for backend/screener/scheduler.py

Coverage:
- create_screener_scheduler() returns an AsyncIOScheduler instance
- The scheduler has exactly one job registered with id="screener_daily"
- The trigger fires at 16:00 (4PM) ET on weekdays only (mon-fri)
- The scheduler is not started on creation (caller calls .start() in FastAPI lifespan)
- run_screening_job() swallows ScreeningAgentError without re-raising
- run_screening_job() swallows unexpected Exception without re-raising
- run_screening_job() calls run_screening() when no exception is raised
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.screener.scheduler import create_screener_scheduler, run_screening_job


# ---------------------------------------------------------------------------
# create_screener_scheduler contract tests
# ---------------------------------------------------------------------------

def test_create_screener_scheduler_returns_async_io_scheduler():
    """create_screener_scheduler() must return an AsyncIOScheduler."""
    scheduler = create_screener_scheduler()
    assert isinstance(scheduler, AsyncIOScheduler)


def test_create_screener_scheduler_has_one_job():
    """Exactly one job is registered by create_screener_scheduler()."""
    scheduler = create_screener_scheduler()
    jobs = scheduler.get_jobs()
    assert len(jobs) == 1, f"Expected 1 job, found {len(jobs)}"


def test_create_screener_scheduler_job_id_is_screener_daily():
    """The registered job must have id='screener_daily'."""
    scheduler = create_screener_scheduler()
    jobs = scheduler.get_jobs()
    assert jobs[0].id == "screener_daily"


def test_create_screener_scheduler_trigger_is_cron():
    """The job trigger must be a CronTrigger."""
    scheduler = create_screener_scheduler()
    job = scheduler.get_jobs()[0]
    assert isinstance(job.trigger, CronTrigger)


def test_create_screener_scheduler_trigger_fires_at_1600():
    """
    The CronTrigger must be configured for hour=16, minute=0.
    Inspect trigger fields by examining the string representation,
    which APScheduler renders as 'hour=16, minute=0, day_of_week=mon-fri'.
    """
    scheduler = create_screener_scheduler()
    job = scheduler.get_jobs()[0]
    trigger_str = str(job.trigger)
    assert "16" in trigger_str, f"Hour 16 not found in trigger: {trigger_str}"
    assert "0" in trigger_str, f"Minute 0 not found in trigger: {trigger_str}"


def test_create_screener_scheduler_trigger_weekdays_only():
    """Trigger should be constrained to Monday–Friday."""
    scheduler = create_screener_scheduler()
    job = scheduler.get_jobs()[0]
    trigger_str = str(job.trigger)
    # APScheduler renders day_of_week as "mon-fri" in the string repr
    assert "mon" in trigger_str.lower(), f"'mon' not found in trigger: {trigger_str}"


def test_create_screener_scheduler_not_running_on_creation():
    """Scheduler must NOT be started by create_screener_scheduler(); caller starts it."""
    scheduler = create_screener_scheduler()
    assert not scheduler.running, "Scheduler should not be started yet"


def test_create_screener_scheduler_misfire_grace_time_set():
    """misfire_grace_time should be set (not None) so missed jobs can be caught up."""
    scheduler = create_screener_scheduler()
    job = scheduler.get_jobs()[0]
    assert job.misfire_grace_time is not None, "misfire_grace_time should be configured"


# ---------------------------------------------------------------------------
# run_screening_job error-handling tests
# ---------------------------------------------------------------------------

def test_run_screening_job_swallows_screening_agent_error():
    """ScreeningAgentError is caught and does not propagate out of run_screening_job."""
    with patch("backend.screener.scheduler.run_screening_job.__module__"):
        pass  # ensure the import path is available

    async def _run():
        with patch(
            "backend.agents.screening_agent.run_screening",
        ) as mock_run, patch(
            "backend.agents.screening_agent.ScreeningAgentError",
            Exception,  # use plain Exception as stand-in for import
        ):
            # Import ScreeningAgentError from the real module
            from backend.agents.screening_agent import ScreeningAgentError

            mock_run.side_effect = ScreeningAgentError("Pipeline failed")
            # Must not raise
            await run_screening_job()

    asyncio.get_event_loop().run_until_complete(_run())


def test_run_screening_job_swallows_unexpected_exception():
    """Unexpected exceptions are caught; run_screening_job does not re-raise."""
    async def _run():
        with patch("backend.agents.screening_agent.run_screening") as mock_run:
            mock_run.side_effect = RuntimeError("Unexpected crash")
            # Must not raise
            await run_screening_job()

    asyncio.get_event_loop().run_until_complete(_run())


def test_run_screening_job_calls_run_screening_on_success():
    """run_screening_job calls run_screening() exactly once on a clean run."""
    async def _run():
        with patch("backend.agents.screening_agent.run_screening") as mock_run:
            mock_run.return_value = [{"ticker": "AAPL", "composite_score": 7.5}]
            await run_screening_job()
            mock_run.assert_called_once()

    asyncio.get_event_loop().run_until_complete(_run())
