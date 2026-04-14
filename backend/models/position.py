"""
Position — represents a live or historical position.

Used by Portfolio Agent (sizing), Risk Agent (monitoring), and Execution Agent (orders).
Skill integration: /private-equity:returns populates `return_scenarios` at approval gate.
"""

from typing import Literal, Optional
from pydantic import BaseModel, Field


class ReturnScenarios(BaseModel):
    """IRR/MOIC sensitivity table — populated by /private-equity:returns at sizing step."""
    bull_irr: float
    base_irr: float
    bear_irr: float
    bull_moic: float
    base_moic: float
    bear_moic: float


class Position(BaseModel):
    ticker: str
    direction: Literal["LONG", "SHORT"]
    shares: float
    entry_price: float
    current_price: float
    pnl: float  # unrealized P&L in dollars
    pnl_pct: float  # unrealized P&L as a percentage

    # Sizing inputs (from Portfolio Agent + Research Agent)
    conviction_score: float = Field(ge=0.0, le=10.0)
    kelly_fraction: float  # 25% fractional Kelly
    pct_of_portfolio: float  # fraction of portfolio NAV, max 0.15

    # Stop structure (3-tier, see CLAUDE.md)
    stop_tier1: Optional[float] = None  # position-level stop price
    stop_tier2: Optional[float] = None  # strategy/sector stop price
    stop_tier3: Optional[float] = None  # portfolio drawdown threshold

    # Skill-extension: /private-equity:returns
    return_scenarios: Optional[ReturnScenarios] = None

    # Metadata
    memo_id: Optional[str] = None  # FK to memos.id in Supabase
    opened_at: Optional[str] = None  # ISO datetime
    next_earnings_date: Optional[str] = None  # ISO date YYYY-MM-DD; from portfolio_agent via fmp_data
