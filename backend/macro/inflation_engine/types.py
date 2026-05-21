"""Types for the inflation engine's mixed-frequency handling.

PR 2 of the inflation engine redesign (docs/inflation_engine_design.md §2.3).

IndicatorMeta carries freshness metadata for a single indicator series.
The confidence field (computed externally via staleness.py) lets the aggregator
downweight stale monthly inputs rather than treating them equal to fresh
daily ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional


# Map each frequency label to its canonical period length in days.
# Used for:
#   1. Computing `is_stale` (staleness_days > 1.5 × native_frequency_days)
#   2. Choosing the decay halflife in staleness.py
FREQUENCY_DAYS: dict[str, int] = {
    "daily": 1,
    "weekly": 7,
    "monthly": 30,
    "quarterly": 90,
}

NativeFrequency = Literal["daily", "weekly", "monthly", "quarterly"]


@dataclass
class IndicatorMeta:
    """Freshness metadata for a single inflation indicator series.

    Fields
    ------
    series_id:
        The vendor/FRED series identifier, e.g. ``"CPIAUCSL"``.
    native_frequency:
        How often this series is published: ``"daily"``, ``"weekly"``,
        ``"monthly"``, or ``"quarterly"``.
    last_release_dt:
        Timestamp of the most recent observation. For FRED monthly series
        this is the first-of-month date of the latest data point, which
        reflects the release lag accurately enough for our purposes.
    next_expected_release:
        Calendar estimate of the next publication date; may be None when
        unknown (e.g. ad-hoc FRED revisions). Used for display / alerting
        only — not used in confidence calculation.
    staleness_days:
        Integer number of days between ``last_release_dt`` and the
        reference "today" passed by the caller. Must be ≥ 0.
    is_stale:
        True when ``staleness_days > 1.5 × FREQUENCY_DAYS[native_frequency]``.
        A monthly series becomes stale after ~45 days; a daily series after
        ~1.5 days.
    """

    series_id: str
    native_frequency: NativeFrequency
    last_release_dt: datetime
    staleness_days: int
    next_expected_release: Optional[datetime] = None

    @property
    def is_stale(self) -> bool:
        """True when staleness exceeds 1.5 × the native publication period."""
        threshold = 1.5 * FREQUENCY_DAYS[self.native_frequency]
        return self.staleness_days > threshold

    def __post_init__(self) -> None:
        if self.staleness_days < 0:
            raise ValueError(
                f"IndicatorMeta.staleness_days must be ≥ 0, got {self.staleness_days}"
            )
        if self.native_frequency not in FREQUENCY_DAYS:
            raise ValueError(
                f"IndicatorMeta.native_frequency must be one of "
                f"{list(FREQUENCY_DAYS)}, got '{self.native_frequency}'"
            )
