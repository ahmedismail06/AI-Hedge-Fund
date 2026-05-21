"""Surprise (actual-vs-consensus) inflation layer — PR3 stub.

PR 3 of the inflation engine redesign (docs/inflation_engine_design.md §2.5).

This layer is a deliberate no-op in PR3.  It returns confidence=0 so that the
aggregator drops it and redistributes its 10% weight proportionally to the
remaining three layers.

PR4 will implement the real surprise layer using ``ManualConsensusSource``
(Supabase ``inflation_consensus`` table) with vendor adapters slotting in via
the ``ConsensusSource`` Protocol.

The stub is implemented as a full module rather than a lambda so that:
1. The import tree is complete and tests can exercise the stub.
2. PR4 can slot in real logic without changing any call sites.
"""

from __future__ import annotations

from backend.macro.inflation_engine.types import InflationSnapshot, LayerOutput

_STUB_NOTE = "surprise layer not active until PR4"


def compute(snapshot: InflationSnapshot) -> LayerOutput:  # noqa: ARG001
    """Return a zero-confidence LayerOutput — surprise layer is a stub in PR3.

    Parameters
    ----------
    snapshot:
        InflationSnapshot (ignored in PR3).

    Returns
    -------
    LayerOutput
        ``score=0.0``, ``confidence=0.0`` — the aggregator drops this layer
        and redistributes its base weight (10%) proportionally to the
        remaining layers.
    """
    return LayerOutput(
        score=0.0,
        confidence=0.0,
        contributors=[],
        notes=[_STUB_NOTE],
    )
