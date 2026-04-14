"""
Risk layer Pydantic schemas — local to backend/risk/.

These are internal event/status objects used within the risk pipeline.
Shared output models (RiskAlert, PortfolioMetrics) live in backend/models/risk.py.

StopEvent      — emitted by stop_loss.py when a tier threshold is breached.
ExposureBreach — emitted by exposure_monitor.py when gross/net exposure drifts.
RiskStatus     — lightweight summary returned by GET /risk/status.
"""

from typing import Literal, Optional
from pydantic import BaseModel


class StopEvent(BaseModel):
    """Fired when a position/sector/portfolio stop threshold is crossed."""
    ticker: Optional[str] = None          # None for Tier 2 (sector) and Tier 3 (portfolio)
    tier: Literal[1, 2, 3]               # 1=position, 2=sector, 3=portfolio
    entry_price: Optional[float] = None  # None for Tier 2/3
    current_price: Optional[float] = None
    stop_price: Optional[float] = None   # pre-computed stop level at time of check
    pct_move: float                       # pnl_pct at time of trigger (negative = loss)
    regime: str                           # macro regime at time of check
    sector: Optional[str] = None          # populated for Tier 2 events
    approaching: bool = False             # True when approaching stop (WARN) vs breached


class ExposureBreach(BaseModel):
    """Fired when portfolio gross or net exposure drifts past regime cap."""
    current_gross: float                  # current gross exposure as fraction of NAV
    cap_gross: float                      # regime-gated gross cap (e.g. 0.80 for Risk-Off)
    current_net: float                    # current net long exposure as fraction of NAV
    cap_net: float                        # regime-gated net cap
    severity: Literal["WARN", "BREACH"]  # WARN if within 10% of cap, BREACH if exceeded
    regime: str


class RiskStatus(BaseModel):
    """Lightweight summary surfaced by GET /risk/status."""
    active_alerts_count: int
    critical_count: int
    warn_count: int
    breach_count: int
    latest_metrics_date: Optional[str] = None  # YYYY-MM-DD of most recent portfolio_metrics row
    autonomous_mode_suspended: bool
    regime: Optional[str] = None
