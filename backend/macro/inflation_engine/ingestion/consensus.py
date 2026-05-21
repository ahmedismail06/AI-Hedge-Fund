"""ConsensusSource Protocol and NullConsensusSource stub.

PR 3 ships:
  - The ``ConsensusSource`` Protocol (interface only — no live data).
  - ``NullConsensusSource``: always returns an empty list; causes the surprise
    layer to return ``confidence=0``, which the aggregator drops cleanly.

PR 4 will add:
  - ``ManualConsensusSource``: reads from the ``inflation_consensus`` Supabase
    table (populated by hand for backtesting; vendor adapters slot in later).

Design reference: docs/inflation_engine_design.md §2.5
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class ConsensusExpectation:
    """A single consensus expectation for one indicator release.

    Fields
    ------
    indicator:
        Series name, e.g. ``"cpi_yoy"``.
    expected_value:
        Consensus estimate before the release.
    actual_value:
        Actual printed value. None if not yet released.
    release_dt:
        When the release occurred (or is scheduled).
    source:
        Vendor / data source identifier, e.g. ``"Manual"`` or
        ``"TradingEconomics"``.
    """

    indicator: str
    expected_value: float
    actual_value: Optional[float]
    release_dt: datetime
    source: str


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ConsensusSource(Protocol):
    """Interface for consensus-expectation data sources.

    Any adapter (Manual, TradingEconomics, Econoday, …) must implement this
    single method.  The surprise layer calls it on every scoring run.
    """

    def get_expectations(self, since: datetime) -> list[ConsensusExpectation]:
        """Return all consensus expectations with release_dt >= ``since``."""
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# NullConsensusSource — PR3 default (returns confidence=0 from surprise layer)
# ---------------------------------------------------------------------------


class NullConsensusSource:
    """Always returns an empty list of expectations.

    This causes the surprise layer to return ``LayerOutput(score=0.0,
    confidence=0.0, ...)``, which the aggregator treats as "no data" and
    drops, redistributing the surprise layer's weight proportionally to
    the remaining three layers.

    This is the default in PR3.  Swap to ``ManualConsensusSource`` in PR4
    without changing any other code.
    """

    def get_expectations(self, since: datetime) -> list[ConsensusExpectation]:  # noqa: ARG002
        return []
