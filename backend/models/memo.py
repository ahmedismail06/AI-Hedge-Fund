"""
InvestmentMemo — output schema for the Research Agent.

Core fields are produced by the LLM (see research_agent.py SYSTEM_PROMPT).
Optional extension fields are populated by skill integrations (see SKILLS_INTEGRATION.md).
"""

from typing import Literal, Optional
from pydantic import BaseModel, Field


class FinancialHealth(BaseModel):
    revenue_trend: Literal["growing", "stable", "declining", "unknown"]
    margin_trend: Literal["expanding", "stable", "contracting", "unknown"]
    debt_level: Literal["low", "moderate", "high", "unknown"]
    fcf: Literal["strong", "neutral", "weak", "unknown"]
    cash_runway_months: Optional[float] = None  # Required for sub-$2B; null for larger


class EarningsScenarios(BaseModel):
    """Pre-earnings bull/base/bear scenarios — populated by /equity-research:earnings-preview."""
    bull: str
    base: str
    bear: str
    report_date: Optional[str] = None  # YYYY-MM-DD


class InvestmentMemo(BaseModel):
    # ── Core fields (LLM-produced) ────────────────────────────────────────────
    ticker: str
    date: str  # YYYY-MM-DD
    sector: Optional[str] = None
    company_overview: str
    bull_thesis: list[str]
    bear_thesis: list[str] = Field(min_length=4)
    key_risks: list[str]
    catalysts: list[str]
    financial_health: FinancialHealth
    macro_sensitivity: str
    verdict: Literal["LONG", "SHORT", "AVOID"]
    conviction_score: float = Field(ge=0.0, le=10.0)
    conviction_score_rationale: str  # One sentence using the rubric
    valuation_note: str              # Specific metric vs peers/history, or "unavailable"
    variant_perception: str          # "Market believes X. We believe Y because Z."
    repricing_catalyst: str          # "Event is X, expected Y, which reveals Z."
    suggested_position_size: Literal["small", "medium", "large", "skip"]
    summary: str

    # ── Skill-extension fields (optional) ────────────────────────────────────
    # /equity-research:thesis or /financial-analysis:dcf-model
    price_target: Optional[float] = None
    price_target_basis: Optional[str] = None  # e.g. "DCF (WACC 10%, terminal 3%)"

    # /equity-research:earnings-preview
    earnings_scenarios: Optional[EarningsScenarios] = None

    # /private-equity:unit-economics  (SaaS tickers only)
    unit_economics: Optional[dict] = None  # ARR cohorts, LTV/CAC, retention

    # /financial-analysis:competitive-analysis
    competitive_position: Optional[str] = None

    # Red team — second LLM call that argues against the bull thesis
    red_team_risks: Optional[list[str]] = None
