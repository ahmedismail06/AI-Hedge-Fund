"""
Risk Management Agent (Component 5).

Two public entry points called by APScheduler in backend/main.py:

  run_risk_monitor()    — called every 60 seconds; coordinates one monitor cycle.
  run_nightly_metrics() — called nightly at 22:00 ET Mon–Fri; computes and
                          persists PortfolioMetrics to Supabase.

Per architecture.md, a CRITICAL alert produced here blocks all new trade
approvals at the orchestrator level (checked via GET /risk/alerts/critical).
"""

import logging
import os
from typing import Optional

from dotenv import load_dotenv
from supabase import Client, create_client

from backend.models.risk import PortfolioMetrics
from backend.risk.metrics import compute_nightly_metrics
from backend.risk.monitor import run_monitor_cycle, write_heartbeat

load_dotenv()

logger = logging.getLogger(__name__)


class RiskAgentError(Exception):
    pass


# ──────────────────────────────────────────────────────────────────────────────
# Supabase client (module-level singleton)
# ──────────────────────────────────────────────────────────────────────────────

def _get_supabase() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise RiskAgentError("SUPABASE_URL and SUPABASE_KEY must be set in .env")
    return create_client(url, key)


# ──────────────────────────────────────────────────────────────────────────────
# Regime reader (same pattern as portfolio_agent._read_macro_regime)
# ──────────────────────────────────────────────────────────────────────────────

def _read_macro_regime(supabase: Client) -> str:
    """Read the latest macro regime from macro_briefings. Defaults to Transitional."""
    try:
        resp = (
            supabase
            .table("macro_briefings")
            .select("regime")
            .order("date", desc=True)
            .limit(1)
            .execute()
        )
        if resp.data:
            return resp.data[0]["regime"]
    except Exception as exc:
        logger.warning("_read_macro_regime failed: %s — defaulting to Transitional", exc)
    return "Transitional"


# ──────────────────────────────────────────────────────────────────────────────
# Public entry points
# ──────────────────────────────────────────────────────────────────────────────

def startup_heartbeat() -> bool:
    """
    Write a heartbeat row to risk_alerts to confirm Supabase connectivity.
    Call once from backend/main.py lifespan startup.

    Returns True if successful (table reachable), False otherwise.
    """
    try:
        supabase = _get_supabase()
        return write_heartbeat(supabase)
    except Exception as exc:
        logger.error("startup_heartbeat failed: %s", exc)
        return False


async def run_risk_monitor() -> dict:
    """
    Execute one 60-second risk monitoring cycle.

    Called by APScheduler IntervalTrigger(seconds=60) in backend/main.py.

    Returns:
        Summary dict: {positions_checked, alerts_fired, critical_count, skipped}
    """
    try:
        supabase = _get_supabase()
        regime = _read_macro_regime(supabase)
        return run_monitor_cycle(supabase, regime)
    except RiskAgentError:
        raise
    except Exception as exc:
        logger.error("run_risk_monitor failed: %s", exc, exc_info=True)
        raise RiskAgentError(str(exc)) from exc


async def run_nightly_metrics() -> Optional[PortfolioMetrics]:
    """
    Compute and persist nightly PortfolioMetrics.

    Called by APScheduler CronTrigger(hour=22, day_of_week='mon-fri') in backend/main.py.

    Returns:
        PortfolioMetrics object (fields may be None if < 5 closed positions).
    """
    try:
        supabase = _get_supabase()
        return compute_nightly_metrics(supabase)
    except RiskAgentError:
        raise
    except Exception as exc:
        logger.error("run_nightly_metrics failed: %s", exc, exc_info=True)
        raise RiskAgentError(str(exc)) from exc
