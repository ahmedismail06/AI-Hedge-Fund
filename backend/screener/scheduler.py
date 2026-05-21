"""
Screener Scheduler — triggers the full screening pipeline daily at 4PM ET.

Register via create_screener_scheduler() in backend/main.py alongside
the macro scheduler.

Pattern mirrors backend/macro/scheduler.py.
"""

import asyncio
import logging
import time
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.notifications.events import notify_event

logger = logging.getLogger(__name__)


async def run_peer_multiples_job() -> None:
    """
    Compute and upsert peer EV/Revenue multiples from the latest screener universe.
    Called after run_screening_job() completes. Never propagates exceptions.
    """
    logger.info("Peer multiples refresh starting")
    try:
        from backend.financial_modeling.relative_valuation import compute_peer_multiples_for_universe
        from backend.db.utils import get_supabase_client

        client = get_supabase_client()

        # Find the most recent screener run date
        date_resp = (
            client.table("watchlist")
            .select("run_date")
            .order("run_date", desc=True)
            .limit(1)
            .execute()
        )
        if not date_resp.data:
            logger.warning("run_peer_multiples_job: no watchlist rows found")
            return

        latest_date = date_resp.data[0]["run_date"]

        # Pull active universe (all non-excluded tickers from latest run)
        universe_resp = (
            client.table("watchlist")
            .select("ticker,sector,market_cap_m")
            .eq("run_date", latest_date)
            .execute()
        )
        active_tickers = universe_resp.data or []
        # Filter out rows missing sector or market_cap_m
        active_tickers = [
            t for t in active_tickers
            if t.get("sector") and t.get("market_cap_m") and t.get("ticker")
        ]

        logger.info(
            "Peer multiples refresh: %d tickers from run_date %s",
            len(active_tickers), latest_date,
        )
        compute_peer_multiples_for_universe(active_tickers)
        logger.info("Peer multiples refresh complete")

    except Exception as exc:
        logger.exception("Peer multiples refresh failed: %s", exc)


async def run_short_universe_rebuild_job() -> None:
    """
    Weekly rebuild of the short-universe cache (~30–60 min). Runs in a worker
    thread so the asyncio loop and other scheduled jobs aren't blocked. The
    daily short screener reads this cache; no rebuild = empty TRIGGER_SCORER
    output that day.
    """
    from backend.screener.short_universe import _fetch_universe_fresh, _save_cache

    started = time.monotonic()
    logger.info("=== Short universe weekly rebuild starting ===")
    try:
        rows = await asyncio.to_thread(_fetch_universe_fresh)
        elapsed_m = (time.monotonic() - started) / 60.0
        if rows:
            _save_cache(rows)
            logger.info(
                "=== Short universe rebuild complete | %d rows | %.1fm ===",
                len(rows), elapsed_m,
            )
        else:
            # Cache deliberately not overwritten so a stale-but-real cache wins
            # over an empty rebuild (e.g. transient FMP/Polygon outage).
            logger.error(
                "Short universe rebuild returned 0 rows after %.1fm — cache NOT overwritten",
                elapsed_m,
            )
    except Exception as exc:
        logger.exception("Short universe rebuild failed: %s", exc)


async def run_short_screening_job() -> None:
    """Async wrapper for the short screening pipeline. Runs after the long screener."""
    from backend.agents.short_screening_agent import run_short_screening
    logger.info("Short screener job starting (post-long-screener trigger)")
    try:
        results = run_short_screening()
        logger.info("Short screener complete — %d candidates selected", len(results))
    except Exception as exc:
        logger.exception("Short screener job failed: %s", exc)


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
        top_tickers = [{"ticker": r["ticker"], "score": r["composite_score"]} for r in results[:5]]
        notify_event("SCREENING_COMPLETE", {
            "qualified_count": len(results),
            "top_tickers": top_tickers,
        })
        if top_tickers:
            notify_event("RESEARCH_QUEUED", {
                "tickers": [t["ticker"] for t in top_tickers],
            })
    except ScreeningAgentError as exc:
        logger.error("Screener pipeline error: %s", exc)
    except Exception as exc:
        logger.exception("Unexpected screener job failure: %s", exc)

    # Refresh peer multiples after screener finishes (needed for relative valuation at memo time)
    await run_peer_multiples_job()
    # Short screening runs after watchlist is written — sources from today's watchlist rows
    await run_short_screening_job()


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
    scheduler.add_job(
        run_short_universe_rebuild_job,
        trigger=CronTrigger(
            day_of_week="sun",
            hour=2,
            minute=0,
            timezone=ZoneInfo("America/New_York"),
        ),
        id="short_universe_weekly",
        replace_existing=True,
        misfire_grace_time=6 * 3600,  # generous — rebuild itself takes ~30–60m
        coalesce=True,
        max_instances=1,
    )
    logger.info(
        "Screener scheduler configured — daily 16:00 ET (Mon–Fri); "
        "short_universe rebuild Sun 02:00 ET"
    )
    return scheduler
