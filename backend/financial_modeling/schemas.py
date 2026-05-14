"""
Financial Modeling schemas — Pydantic models for DCF output and earnings quality.
"""

from typing import Optional, Literal
from pydantic import BaseModel, Field, ConfigDict


class DCFScenario(BaseModel):
    revenue_growth_rate: float
    ebitda_margin: float
    price_target: float


class DCFResult(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    bull: DCFScenario
    base: DCFScenario
    bear: DCFScenario
    wacc: float
    terminal_growth: float
    shares_outstanding: Optional[float] = None
    current_price: Optional[float] = None
    key_drivers: list[str]
    unavailable: bool = False
    unavailable_reason: Optional[str] = None


class EarningsQualityResult(BaseModel):
    beneish_gate: str  # EXCLUDED | FLAGGED | CLEAN | INSUFFICIENT_DATA
    m_score: Optional[float] = None
    accruals_ratio: Optional[float] = None
    revenue_quality_flag: Optional[str] = None
    quality_grade: Literal["A", "B", "C", "D", "N/A"] = "N/A"
    unavailable: bool = False


class RelativeValuationResult(BaseModel):
    """EV/Revenue + EV/Gross Profit peer comps — used when DCF gate fires."""
    bull_target: float
    base_target: float
    bear_target: float
    primary_multiple: str = "EV/Revenue"
    ev_revenue_used: float           # peer median EV/Revenue applied
    ev_gross_profit_secondary: Optional[float] = None  # peer median EV/GP for reference
    peer_count: int
    peer_median_ev_revenue: float
    peer_25th_ev_revenue: float
    peer_75th_ev_revenue: float
    basis: str                       # 1-2 sentence description of what was applied
    skipped_reason: Optional[str] = None
    unavailable: bool = False


class FinancialModelOutput(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    ticker: str
    run_date: str  # YYYY-MM-DD
    dcf: DCFResult
    earnings_quality: EarningsQualityResult
    relative_valuation: Optional[RelativeValuationResult] = None
    valuation_method: str = "dcf"   # "dcf" | "relative" | "none"
    summary: str
    model_json: dict
