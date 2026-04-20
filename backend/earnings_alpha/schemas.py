from __future__ import annotations

from typing import Literal, Optional

try:
    from pydantic import BaseModel, field_validator
    _HAS_PYDANTIC_V2 = True
except ImportError:
    from pydantic import BaseModel, validator as field_validator  # type: ignore
    _HAS_PYDANTIC_V2 = False


class PreEarningsSizing(BaseModel):
    signal: Literal["SIZE_UP", "HOLD", "REDUCE"]
    internal_eps_estimate: Optional[float] = None
    consensus_eps: Optional[float] = None
    # (internal - consensus) / |consensus|; None when either estimate unavailable
    spread_pct: Optional[float] = None
    conviction_gate_passed: bool
    rationale: str


class DriftHoldState(BaseModel):
    active: bool
    surprise_pct: Optional[float] = None
    hold_until: Optional[str] = None        # ISO date string
    hold_days_remaining: Optional[int] = None


class EarningsAlphaOutput(BaseModel):
    ticker: str
    run_date: str                               # YYYY-MM-DD
    pre_earnings: PreEarningsSizing
    drift_hold: DriftHoldState
    # Derived from last 8 quarters of reactions data
    historical_beat_rate: Optional[float] = None        # fraction of quarters beating consensus
    avg_post_beat_reaction_5d: Optional[float] = None   # avg 5-day return on beat quarters
    summary: str                                # formatted block injected into synthesis
    unavailable: bool = False
    unavailable_reason: Optional[str] = None
