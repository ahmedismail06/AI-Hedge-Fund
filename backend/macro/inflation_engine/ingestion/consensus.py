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

import logging
from datetime import datetime

from backend.db.utils import get_supabase_client
from backend.macro.inflation_engine.types import ConsensusExpectation, ConsensusSource

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ManualConsensusSource — PR4 implementation
# ---------------------------------------------------------------------------


class ManualConsensusSource:
    """Reads from the ``inflation_consensus`` Supabase table.

    The table is populated by hand or by scheduled vendor-scraping scripts.
    """

    def get_expectations(self, since: datetime) -> list[ConsensusExpectation]:
        """Fetch expectations from Supabase."""
        try:
            supabase = get_supabase_client()
            # We use .isoformat() to ensure proper date/time comparison in Supabase/PostgREST.
            response = (
                supabase.table("inflation_consensus")
                .select("*")
                .gte("release_dt", since.isoformat())
                .order("release_dt", desc=True)
                .execute()
            )

            results: list[ConsensusExpectation] = []
            for row in response.data:
                results.append(
                    ConsensusExpectation(
                        indicator=row["indicator"],
                        expected_value=float(row["expected_value"]),
                        actual_value=float(row["actual_value"])
                        if row["actual_value"] is not None
                        else None,
                        release_dt=datetime.fromisoformat(
                            row["release_dt"].replace("Z", "+00:00")
                        ),
                        source=row["source"],
                    )
                )

            logger.debug(
                "ManualConsensusSource: fetched %d expectations since %s",
                len(results),
                since.date(),
            )
            return results

        except Exception as e:
            logger.error("ManualConsensusSource: failed to fetch from Supabase: %s", e)
            return []


# ---------------------------------------------------------------------------
# NullConsensusSource — PR3 default (returns confidence=0 from surprise layer)
# ---------------------------------------------------------------------------


class NullConsensusSource:
    """Always returns an empty list of expectations.

    This causes the surprise layer to return ``LayerOutput(score=0.0,
    confidence=0.0, ...)``, which the aggregator treats as "no data" and
    drops, redistributing the surprise layer's weight proportionally to
    the remaining three layers.
    """

    def get_expectations(self, since: datetime) -> list[ConsensusExpectation]:  # noqa: ARG002
        return []
