"""
FastAPI application entry point.
Registers all agent routers and starts the server.
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.agents.research_agent import run_research, ResearchAgentError
from backend.memory.vector_store import (
    store_memo,
    get_memo,
    get_all_memos,
    get_watchlist,
    update_memo_status,
)

app = FastAPI(title="AI Hedge Fund API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
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
    status: str  # APPROVED | REJECTED | WATCHLIST


@app.post("/research/{memo_id}/status")
def update_status(memo_id: str, body: StatusUpdate):
    """Updates the review status of a memo."""
    valid = {"APPROVED", "REJECTED", "WATCHLIST"}
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


# WATCHFILES_IGNORE_PATHS=".venv" uvicorn backend.main:app --reload --reload-dir backend 