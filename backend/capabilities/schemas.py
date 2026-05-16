"""
Capability schemas — what the system is allowed to do given current NAV.
"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel


class Capabilities(BaseModel):
    nav: float
    nav_trailing_30d: float

    # Long side — always enabled
    longs_enabled: bool = True
    long_universe_min_cap: float = 50.0   # $M market cap
    long_universe_max_cap: float = 2000.0  # $M market cap

    # Short side
    shorts_enabled: bool
    short_universe_min_cap: Optional[float]  # None when shorts disabled
    short_factor_active: bool  # short interest signal scored in screener

    # Exposure limits
    max_position_pct: float = 0.15  # hard 15% cap — always active
    max_gross_exposure_pct: float = 2.00  # 200% absolute ceiling — always active
    max_net_exposure_pct: Optional[float]  # None when shorts disabled

    # Alt data
    alt_data_permitted: bool

    # Audit trace
    capability_tier: str  # tier_0 | tier_25k | tier_50k | tier_100k | tier_250k
    unlocked_reasons: list[str]
