"""
Risk models — shared between Risk Agent and dashboard API.

RiskAlert: individual triggered alert (position stop, sector, portfolio drawdown).
PortfolioMetrics: nightly computed stats (Sharpe, VaR, drawdown, etc.).
"""

from typing import Literal, Optional
from pydantic import BaseModel


class RiskAlert(BaseModel):
    alert_id: str
    timestamp: str  # ISO datetime
    ticker: Optional[str] = None  # None for portfolio-level alerts
    tier: Literal[1, 2, 3]  # 1=position, 2=strategy/sector, 3=portfolio
    trigger: str  # human-readable description of what was breached
    regime: str  # macro regime at time of alert (tightened in Risk-Off)
    resolved: bool = False


class PortfolioMetrics(BaseModel):
    """Nightly computed metrics — see CLAUDE.md for full list."""
    date: str  # YYYY-MM-DD
    sharpe_ratio: Optional[float] = None
    sortino_ratio: Optional[float] = None
    max_drawdown: Optional[float] = None
    var_95: Optional[float] = None  # Value at Risk 95%
    var_99: Optional[float] = None  # Value at Risk 99%
    beta: Optional[float] = None
    calmar_ratio: Optional[float] = None
    gross_exposure: Optional[float] = None  # Risk-On cap: 150%; Risk-Off: 80%
    net_exposure: Optional[float] = None
