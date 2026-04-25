"""
EarningsAlpha API — pre-earnings sizing signals and drift-hold state.

Endpoints:
  GET  /earnings-alpha/{ticker}/latest     — most recent earnings_events row for a ticker
  GET  /earnings-alpha/{ticker}/drift-hold — current drift-hold state
  POST /earnings-alpha/run/{ticker}        — manual trigger: fetch reactions + run pipeline
"""

from dotenv import load_dotenv

load_dotenv()

import logging
import os

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/earnings-alpha", tags=["earnings-alpha"])


from backend.db.utils import get_supabase_client


def _get_client():
    return get_supabase_client()


# ── GET /earnings-alpha/{ticker}/latest ───────────────────────────────────────

@router.get("/{ticker}/latest")
async def get_latest_earnings_event(ticker: str) -> dict:
    """Return the most recent earnings_events row for a ticker."""
    try:
        resp = (
            _get_client()
            .table("earnings_events")
            .select("*")
            .eq("ticker", ticker.upper())
            .order("event_date", desc=True)
            .limit(1)
            .execute()
        )
        if not resp.data:
            raise HTTPException(
                status_code=404, detail=f"No earnings events found for {ticker.upper()}"
            )
        return resp.data[0]
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("get_latest_earnings_event(%s): %s", ticker.upper(), exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── GET /earnings-alpha/{ticker}/drift-hold ───────────────────────────────────

@router.get("/{ticker}/drift-hold")
async def get_drift_hold_state(ticker: str) -> dict:
    """Return the current drift-hold state for a ticker."""
    try:
        from backend.earnings_alpha.drift_manager import get_active_drift_hold
        state = get_active_drift_hold(ticker.upper())
        return state.model_dump()
    except Exception as exc:
        logger.error("get_drift_hold_state(%s): %s", ticker.upper(), exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── POST /earnings-alpha/run/{ticker} ─────────────────────────────────────────

@router.post("/run/{ticker}")
async def trigger_earnings_alpha_run(ticker: str) -> dict:
    """
    Manual trigger: fetch earnings reactions + FMP data, run EarningsAlpha pipeline.
    """
    try:
        from backend.fetchers.earnings_reactions import get_earnings_reactions
        from backend.fetchers.fmp_fetcher import fetch_fmp
        from backend.earnings_alpha.runner import run_earnings_alpha

        reactions = get_earnings_reactions(ticker.upper())
        fmp_data = fetch_fmp(ticker.upper())

        # Use stored memo conviction if available; default to 5.0
        conviction: float = 5.0
        try:
            from backend.memory.vector_store import get_memo
            existing = get_memo(ticker.upper())
            if existing and existing.get("conviction_score") is not None:
                conviction = float(existing["conviction_score"])
        except Exception:
            pass

        output = run_earnings_alpha(ticker.upper(), reactions, fmp_data, conviction)
        return output.model_dump()
    except Exception as exc:
        logger.error("trigger_earnings_alpha_run(%s): %s", ticker.upper(), exc)
        raise HTTPException(status_code=500, detail=str(exc))
