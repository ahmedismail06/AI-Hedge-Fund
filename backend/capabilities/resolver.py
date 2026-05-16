"""
Capability resolver — derives what the system can do from trailing 30-day NAV.

Call get_capabilities() from any agent that needs to know the current
capability tier. Pass nav explicitly in tests; omit it in production to
use the cached trailing-30d average.
"""

from __future__ import annotations

import logging
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

from backend.capabilities.schemas import Capabilities
from backend.capabilities.nav_tracker import get_trailing_nav_cached

logger = logging.getLogger(__name__)

# NAV tier thresholds (trailing 30-day average)
_TIER_25K  = 25_000.0
_TIER_50K  = 50_000.0
_TIER_100K = 100_000.0
_TIER_250K = 250_000.0


def get_capabilities(nav: Optional[float] = None) -> Capabilities:
    """
    Derive current system capabilities from trailing 30-day average NAV.

    Args:
        nav: Override NAV for testing. When None, uses get_trailing_nav_cached().

    Returns:
        Capabilities object describing what the system may do right now.
    """
    trailing = nav if nav is not None else get_trailing_nav_cached()
    current  = nav if nav is not None else trailing  # same scalar when provided directly

    reasons: list[str] = [
        "Long-only enabled ($50M–$2B universe, SaaS/Healthcare/Industrials)",
        "15% position cap enforced",
        "200% gross exposure ceiling enforced",
    ]

    if trailing >= _TIER_250K:
        tier = "tier_250k"
        reasons += [
            "Shorts enabled — $50M+ market cap universe (full L/S)",
            "Short interest factor active in screener",
            "Net exposure cap: 20% (overridden by regime caps)",
            "Alt data integration permitted (budget gate separate)",
        ]
        return Capabilities(
            nav=current,
            nav_trailing_30d=trailing,
            shorts_enabled=True,
            short_universe_min_cap=50.0,
            short_factor_active=True,
            max_net_exposure_pct=0.20,
            alt_data_permitted=True,
            capability_tier=tier,
            unlocked_reasons=reasons,
        )

    if trailing >= _TIER_100K:
        tier = "tier_100k"
        reasons += [
            "Shorts enabled — $50M+ market cap universe (full L/S)",
            "Short interest factor active in screener",
            "Net exposure cap: 20% (overridden by regime caps)",
        ]
        return Capabilities(
            nav=current,
            nav_trailing_30d=trailing,
            shorts_enabled=True,
            short_universe_min_cap=50.0,
            short_factor_active=True,
            max_net_exposure_pct=0.20,
            alt_data_permitted=False,
            capability_tier=tier,
            unlocked_reasons=reasons,
        )

    if trailing >= _TIER_50K:
        tier = "tier_50k"
        reasons += [
            "Shorts enabled — $200M+ market cap only",
            "Short interest factor active in screener",
            "Net exposure cap: 20%",
        ]
        return Capabilities(
            nav=current,
            nav_trailing_30d=trailing,
            shorts_enabled=True,
            short_universe_min_cap=200.0,
            short_factor_active=True,
            max_net_exposure_pct=0.20,
            alt_data_permitted=False,
            capability_tier=tier,
            unlocked_reasons=reasons,
        )

    if trailing >= _TIER_25K:
        tier = "tier_25k"
        reasons += [
            "Shorts enabled — $200M+ market cap only",
            "Short interest factor inactive (activates at $50K NAV)",
            "Net exposure cap: 20%",
        ]
        return Capabilities(
            nav=current,
            nav_trailing_30d=trailing,
            shorts_enabled=True,
            short_universe_min_cap=200.0,
            short_factor_active=False,
            max_net_exposure_pct=0.20,
            alt_data_permitted=False,
            capability_tier=tier,
            unlocked_reasons=reasons,
        )

    # Below $25K — long-only
    tier = "tier_0"
    reasons.append("Shorts unavailable (requires ≥$25K trailing NAV)")
    return Capabilities(
        nav=current,
        nav_trailing_30d=trailing,
        shorts_enabled=False,
        short_universe_min_cap=None,
        short_factor_active=False,
        max_net_exposure_pct=None,
        alt_data_permitted=False,
        capability_tier=tier,
        unlocked_reasons=reasons,
    )
