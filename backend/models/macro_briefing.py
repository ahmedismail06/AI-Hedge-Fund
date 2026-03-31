"""
MacroBriefing — output schema for the Macro Agent.

Schema is modeled on /equity-research:morning-note output structure (see SKILLS_INTEGRATION.md).
Produced daily at 7AM ET; stored in Supabase `macro_briefings` table (planned).
"""

from typing import Literal, Optional
from pydantic import BaseModel


class IndicatorScore(BaseModel):
    """Single macroeconomic indicator reading with a directional signal."""

    name: str
    value: float
    signal: Literal["bullish", "neutral", "bearish"]
    note: Optional[str] = None


class SectorTilt(BaseModel):
    """Per-sector positioning — populated by /equity-research:sector."""

    sector: str
    tilt: Literal["overweight", "neutral", "underweight"]
    rationale: str


class UpcomingEvent(BaseModel):
    """Macro calendar event — populated by /equity-research:catalysts."""

    date: str  # YYYY-MM-DD
    event: str
    relevance: Optional[str] = None  # why it matters to the portfolio


class MacroBriefing(BaseModel):
    """Daily macro regime briefing produced by the Macro Agent at 7AM ET."""

    # ── Required fields (quantitative scorer + LLM overlay) ──────────────────
    date: str  # YYYY-MM-DD
    regime: Literal["Risk-On", "Risk-Off", "Transitional", "Stagflation"]
    regime_score: float           # 0–100 composite quantitative score
    override_flag: bool           # True if LLM qualitative overlay overrode quant score
    indicator_scores: list[IndicatorScore]
    qualitative_summary: str      # LLM narrative — 3-5 sentences on macro setup

    # ── Skill-extension fields (/equity-research:morning-note blueprint) ──────
    key_themes: list[str]         # 2-4 bullet points for the morning note header
    portfolio_guidance: str       # regime-specific action summary for Portfolio Agent

    # ── Optional / defaulted fields ───────────────────────────────────────────
    override_reason: Optional[str] = None
    previous_regime: Optional[Literal["Risk-On", "Risk-Off", "Transitional", "Stagflation"]] = None
    regime_changed: bool = False      # True if regime differs from previous day's briefing
    growth_score: float = 0.0         # -1.0 to +1.0; avg of GDP, PMI, Payrolls, Jobless Claims signals
    inflation_score: float = 0.0      # -1.0 to +1.0; high = more inflationary pressure
    fed_score: float = 0.0            # -1.0 to +1.0; positive = accommodative
    stress_score: float = 0.0         # -1.0 to +1.0; positive = high market stress
    regime_confidence: float = 0.0    # 0–10; signal clarity/agreement measure

    # /equity-research:sector
    sector_tilts: Optional[list[SectorTilt]] = None

    # /equity-research:catalysts
    upcoming_events: Optional[list[UpcomingEvent]] = None
