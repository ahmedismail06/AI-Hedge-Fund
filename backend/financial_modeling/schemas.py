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


class FinancialModelOutput(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    ticker: str
    run_date: str  # YYYY-MM-DD
    dcf: DCFResult
    earnings_quality: EarningsQualityResult
    summary: str
    model_json: dict
