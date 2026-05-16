"""
Capabilities API — exposes current NAV-gated system capabilities.
"""

import logging
import os

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException

load_dotenv()

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/capabilities", tags=["capabilities"])


@router.get("")
def get_current_capabilities():
    """Return the current system capabilities derived from trailing 30-day NAV."""
    try:
        from backend.capabilities import get_capabilities
        caps = get_capabilities()
        return caps.model_dump()
    except Exception as exc:
        logger.error("GET /capabilities failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/nav")
def get_nav():
    """Return current live NAV and trailing 30-day average."""
    try:
        from backend.broker.ibkr import get_portfolio_value
        from backend.capabilities.nav_tracker import get_trailing_nav_cached
        current = get_portfolio_value()
        trailing = get_trailing_nav_cached()
        return {"nav_current": current, "nav_trailing_30d": trailing}
    except Exception as exc:
        logger.error("GET /capabilities/nav failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/history")
def get_capabilities_history(limit: int = 30):
    """Return recent capability snapshots (audit trail of tier changes)."""
    try:
        from supabase import create_client
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL / SUPABASE_KEY not set")
        client = create_client(url, key)
        resp = (
            client.table("capability_snapshots")
            .select("*")
            .order("timestamp", desc=True)
            .limit(min(limit, 100))
            .execute()
        )
        return resp.data or []
    except Exception as exc:
        logger.error("GET /capabilities/history failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
