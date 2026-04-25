"""
Financial Modeling API — DCF price targets and earnings quality results.

Endpoints:
  GET  /financial-modeling/{ticker}/latest  — most recent stored model for a ticker
  POST /financial-modeling/run/{ticker}     — manual trigger: fetch data + run DCF + earnings quality
"""

from dotenv import load_dotenv

load_dotenv()

import logging
import os

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/financial-modeling", tags=["financial-modeling"])


from backend.db.utils import get_supabase_client


def _get_client():
    """Return a fresh Supabase client per call to avoid stale connection errors."""
    return get_supabase_client()


# ── GET /financial-modeling/{ticker}/latest ───────────────────────────────────


@router.get("/{ticker}/latest")
async def get_latest_model(ticker: str) -> dict:
    """
    Return the most recent financial model for a ticker from the financial_models table.
    Returns 404 if no model has been run yet for this ticker.
    """
    try:
        resp = (
            _get_client()
            .table("financial_models")
            .select("*")
            .eq("ticker", ticker.upper())
            .order("run_date", desc=True)
            .limit(1)
            .execute()
        )
        if not resp.data:
            raise HTTPException(
                status_code=404,
                detail=f"No financial model found for {ticker.upper()}",
            )
        return resp.data[0]
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("get_latest_model(%s): %s", ticker.upper(), exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── POST /financial-modeling/run/{ticker} ─────────────────────────────────────


@router.post("/run/{ticker}")
async def trigger_model_run(ticker: str) -> dict:
    """
    Manual trigger: fetch market data and run DCF + earnings quality model for a ticker.
    Returns the FinancialModelOutput as a dict.

    Imports are lazy (inside function body) to avoid circular import issues at startup.
    """
    try:
        from backend.fetchers.fmp_fetcher import fetch_fmp
        from backend.financial_modeling.runner import run_financial_model

        fmp_data = fetch_fmp(ticker.upper())
        output = run_financial_model(ticker.upper(), fmp_data)
        return output.model_dump()
    except Exception as exc:
        logger.error("trigger_model_run(%s): %s", ticker.upper(), exc)
        raise HTTPException(status_code=500, detail=str(exc))
