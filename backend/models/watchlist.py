"""
WatchlistEntry — output schema for the Screening Agent.

Schema is informed by /equity-research:screen output structure (see SKILLS_INTEGRATION.md).
Top-5 entries per daily screen run are passed to the Research Agent.
"""

from typing import Literal, Optional
from pydantic import BaseModel, Field


class FactorScores(BaseModel):
    """
    Component scores used in the composite.
    Weights: Quality 50%, Value 30%, Momentum 20% (see CLAUDE.md domain rules).
    """
    quality: float = Field(ge=0.0, le=10.0)
    value: float = Field(ge=0.0, le=10.0)
    momentum: float = Field(ge=0.0, le=10.0)


class WatchlistEntry(BaseModel):
    """Screener output entry — one row per ticker per daily run. Top-5 are queued for research."""

    ticker: str
    date: str  # YYYY-MM-DD — screen run date
    composite_score: float = Field(ge=0.0, le=10.0)  # ≥7.0 to qualify
    factor_scores: FactorScores
    rank: int  # 1 = highest score in today's screen
    market_cap_m: Optional[float] = None  # market cap in $M
    adv_k: Optional[float] = None  # average daily volume in $K
    sector: Optional[str] = None
    beneish_m_score: Optional[float] = None
    beneish_flag: Optional[Literal["EXCLUDED", "FLAGGED", "CLEAN", "INSUFFICIENT_DATA"]] = None
    insider_signal: bool = False
    regime: Optional[str] = None
    queued_for_research: bool = False
    material_event: bool = False
    material_event_reason: Optional[str] = None
    priority: int = 3  # 1=held+material, 2=watchlist+material, 3=nightly, 4=manual
