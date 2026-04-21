"""
FastAPI application entry point.
Registers all agent routers and starts the server.
"""

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler()]
)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from contextlib import asynccontextmanager

from backend.agents.research_agent import run_research, ResearchAgentError
from backend.memory.vector_store import (
    store_memo,
    get_memo,
    get_all_memos,
    get_watchlist,
    update_memo_status,
)
from backend.api.macro import router as macro_router
from backend.api.portfolio import router as portfolio_router
from backend.api.risk import router as risk_router
from backend.api.execution import router as execution_router
from backend.api.orchestrator import router as orchestrator_router
from backend.api.pm import router as pm_router
from backend.api.financial_modeling import router as financial_modeling_router
from backend.api.earnings_alpha import router as earnings_alpha_router
from backend.agents.risk_agent import run_risk_monitor, run_nightly_metrics, startup_heartbeat
from backend.agents.execution_agent import run_execution_cycle

_screener_scheduler = None
_macro_scheduler = None
_research_scheduler = None
_risk_monitor_scheduler = None
_risk_metrics_scheduler = None
_exec_scheduler = None
_pm_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio as _asyncio
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    from apscheduler.triggers.cron import CronTrigger

    global _screener_scheduler, _macro_scheduler, _research_scheduler
    global _risk_monitor_scheduler, _risk_metrics_scheduler, _exec_scheduler, _pm_scheduler

    # NOTE: macro (7AM), screener (4PM), research queue (5:00PM), and ticker events
    # (4:15PM) crons are all owned by _pm_scheduler (create_orchestrator_scheduler).
    # Do NOT start separate schedulers for these — they would fire each pipeline twice.

    # Confirm risk_alerts table is reachable before scheduling
    startup_heartbeat()

    # Risk monitor: every 60 seconds (market-hours guard is inside run_risk_monitor)
    _risk_monitor_scheduler = BackgroundScheduler()
    _risk_monitor_scheduler.add_job(
        lambda: _asyncio.run(run_risk_monitor()),
        trigger=IntervalTrigger(seconds=60),
        id="risk_monitor",
        name="Risk Monitor (60s)",
        replace_existing=True,
    )
    _risk_monitor_scheduler.start()

    # Nightly metrics: 22:00 ET Mon–Fri
    _risk_metrics_scheduler = BackgroundScheduler()
    _risk_metrics_scheduler.add_job(
        lambda: _asyncio.run(run_nightly_metrics()),
        trigger=CronTrigger(
            hour=22, minute=0, day_of_week="mon-fri", timezone="America/New_York"
        ),
        id="risk_nightly_metrics",
        name="Nightly Portfolio Metrics",
        replace_existing=True,
    )
    _risk_metrics_scheduler.start()

    # Execution cycle: every 5 minutes, market-hours guard is inside run_execution_cycle
    _exec_scheduler = BackgroundScheduler()
    _exec_scheduler.add_job(
        run_execution_cycle,
        trigger=IntervalTrigger(seconds=300),
        id="execution_cycle",
        name="Execution Cycle (5m)",
        replace_existing=True,
    )
    _exec_scheduler.start()

    # AI PM Agent: 5-min decision cycle + macro/screener/research crons
    from backend.agents.orchestrator import create_orchestrator_scheduler
    _pm_scheduler = create_orchestrator_scheduler()
    _pm_scheduler.start()

    yield

    for sched in (
        _screener_scheduler,
        _macro_scheduler,
        _research_scheduler,
        _risk_monitor_scheduler,
        _risk_metrics_scheduler,
        _exec_scheduler,
        _pm_scheduler,
    ):
        if sched and sched.running:
            sched.shutdown(wait=False)

    # Disconnect IBKR on shutdown
    try:
        from backend.broker import ibkr as _ibkr
        _ibkr.disconnect()
    except Exception:
        pass


app = FastAPI(title="AI Hedge Fund API", version="0.1.0", lifespan=lifespan)
app.include_router(macro_router)
app.include_router(portfolio_router)
app.include_router(risk_router)
app.include_router(execution_router)
app.include_router(orchestrator_router)
app.include_router(pm_router)
app.include_router(financial_modeling_router)
app.include_router(earnings_alpha_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://ai-hedge-fund-rosy.vercel.app",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
from backend.fetchers.sec_fetcher import fetch_sec_filings
from backend.fetchers.transcript_fetcher import fetch_transcripts
from backend.fetchers.news_fetcher import fetch_news
@app.get("/debug/fetch/{ticker}")
def debug_fetch(ticker: str):
    sec = fetch_sec_filings(ticker)
    trans = fetch_transcripts(ticker)
    news = fetch_news(ticker)
    return {
        "sec_type": type(sec).__name__,
        "sec_chars": len(sec) if isinstance(sec, str) else len(str(sec)) if sec else 0,
        "sec_preview": str(sec)[:200] if sec else "EMPTY",
        "transcript_type": type(trans).__name__,
        "transcript_chars": len(trans) if isinstance(trans, str) else len(str(trans)) if trans else 0,
        "transcript_preview": str(trans)[:200] if trans else "EMPTY",
        "news_type": type(news).__name__,
        "news_chars": len(news) if isinstance(news, str) else len(str(news)) if news else 0,
        "news_preview": str(news)[:200] if news else "EMPTY",
    }
# ── Health ──────────────────────────────────────────────────────────────────


@app.get("/health")
def health():
    return {"status": "ok"}


# ── Research ─────────────────────────────────────────────────────────────────


@app.post("/research/run-queued")
async def trigger_research_queue():
    """Manually fire the research queue poller — processes today's queued_for_research tickers."""
    import asyncio as _asyncio
    from backend.agents.research_scheduler import _poll_research_queue
    loop = _asyncio.get_event_loop()
    processed = await loop.run_in_executor(None, _poll_research_queue)
    return {"queued_tickers_processed": processed}


@app.post("/research/{ticker}")
def trigger_research(ticker: str, use_cache: bool = False):
    """
    Run the research pipeline for a ticker.
    use_cache=true: skips API fetching and re-indexing — uses raw_docs from the most
    recent Supabase memo and queries existing pgvector chunks. Fast (~10s vs 60s+).
    use_cache=false (default): full fetch + index + synthesize pipeline.
    """
    try:
        memo = run_research(ticker, use_cache=use_cache)
    except ResearchAgentError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Research pipeline error: {exc}")

    try:
        memo_id = store_memo(ticker, memo)
        memo["id"] = memo_id
    except Exception as exc:
        # Storage failure should not block the user from seeing the memo
        memo["id"] = None
        memo["_storage_error"] = str(exc)

    return memo


@app.get("/research/history")
def get_history():
    """Returns the last 50 memos across all tickers (summary fields only)."""
    try:
        return get_all_memos(limit=50)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/research/watchlist")
def research_watchlist():
    """Returns all APPROVED and WATCHLIST memos."""
    try:
        return get_watchlist()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/research/{ticker}/latest")
def get_latest_memo(ticker: str):
    """Returns the most recent stored memo for a ticker."""
    try:
        memo = get_memo(ticker)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    if memo is None:
        raise HTTPException(status_code=404, detail=f"No memo found for {ticker.upper()}")
    return memo


class StatusUpdate(BaseModel):
    status: str  # APPROVED | REJECTED | WATCHLIST | DEFERRED | PENDING_PM_REVIEW


@app.post("/research/{memo_id}/status")
def update_status(memo_id: str, body: StatusUpdate):
    """Updates the review status of a memo."""
    valid = {"APPROVED", "REJECTED", "WATCHLIST", "DEFERRED", "PENDING_PM_REVIEW"}
    if body.status not in valid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{body.status}'. Must be one of {valid}",
        )
    try:
        update_memo_status(memo_id, body.status)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"memo_id": memo_id, "status": body.status}



# ── Screening ─────────────────────────────────────────────────────────────────


@app.post("/screening/run")
def trigger_screening(regime: str | None = None):
    """
    Manually trigger a screening run. Regime defaults to Supabase macro_briefings.
    Useful for testing or ad-hoc re-runs outside the scheduled 4PM ET window.
    """
    from backend.agents.screening_agent import run_screening, ScreeningAgentError
    try:
        results = run_screening(regime=regime)
    except ScreeningAgentError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Screening pipeline error: {exc}")
    return {"count": len(results), "results": results}


@app.get("/screening/watchlist")
def get_screener_watchlist(run_date: str | None = None, limit: int = 50, all_time: bool = False):
    """
    Returns today's (or a specific run_date's) screener watchlist from Supabase.
    run_date format: YYYY-MM-DD
    all_time=true: returns top tickers across all dates by composite_score
    """
    from datetime import date as _date
    from backend.memory.vector_store import _get_client
    try:
        client = _get_client()
        if all_time:
            # Fetch a larger pool to ensure we have enough for deduplication
            query = client.table("watchlist").select("*").order("composite_score", desc=True).limit(limit * 5)
            result = query.execute()
            data = result.data or []
            
            seen = set()
            deduped = []
            for row in data:
                if row["ticker"] not in seen:
                    seen.add(row["ticker"])
                    deduped.append(row)
                    if len(deduped) >= limit:
                        break
            return deduped
        else:
            query = client.table("watchlist").select("*").order("rank", desc=False).limit(limit)
            if run_date:
                query = query.eq("run_date", run_date)
            else:
                query = query.eq("run_date", _date.today().isoformat())
        result = query.execute()
        return result.data or []
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# WATCHFILES_IGNORE_PATHS=".venv" uvicorn backend.main:app --reload --reload-dir backend