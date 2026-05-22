"""Thin wrapper that builds an InflationSnapshot from a RawIndicators snapshot.

PR 3 of the inflation engine redesign (docs/inflation_engine_design.md §3).

This module maps field names from RawIndicators (the scorer.py dataclass) into
the InflationSnapshot dataclass that the layer modules consume. It also derives
momentum-ready YoY histories from the recent FRED level history carried on the
RawIndicators object.

Why a separate module rather than building the snapshot inline in scorer.py:
- Keeps scorer.py from growing a long field-mapping block.
- Makes the translation explicit and testable.
- PR4 will extend this to pull sticky_cpi / trimmed_mean_pce from the FredBlock
  directly when those series are available in the block.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Avoid circular imports at runtime; types only needed for type-checkers.
    from backend.macro.scorer import RawIndicators

from backend.macro.inflation_engine.types import InflationSnapshot
from backend.macro.inflation_engine.transforms.momentum import (
    annualised_3m,
    regression_slope,
)


def _compute_yoy_history(level_history: list[float], periods_per_year: int = 12) -> list[float]:
    """Convert a level-history series into a rolling YoY % history."""
    yoy_history: list[float] = []
    if len(level_history) <= periods_per_year:
        return yoy_history

    for idx in range(periods_per_year, len(level_history)):
        prior = level_history[idx - periods_per_year]
        current = level_history[idx]
        if prior == 0:
            continue
        yoy_history.append(((current / prior) - 1.0) * 100.0)
    return yoy_history


def build_snapshot_from_raw(ind: "RawIndicators") -> InflationSnapshot:
    """Build an InflationSnapshot from a RawIndicators instance.

    Fields not present in RawIndicators are left as None. Layers degrade
    confidence proportionally when inputs are absent.

    The ``release_dates`` dict is populated from
    ``ind.inflation_release_dates`` when available.

    Parameters
    ----------
    ind:
        Assembled RawIndicators snapshot from scorer.py.

    Returns
    -------
    InflationSnapshot
        Ready for consumption by layer modules.
    """
    release_dates = dict(ind.inflation_release_dates) if ind.inflation_release_dates else {}
    raw_history = dict(ind.inflation_history) if getattr(ind, "inflation_history", None) else {}

    cpi_yoy_history = _compute_yoy_history(raw_history.get("cpi", []))
    core_cpi_yoy_history = _compute_yoy_history(raw_history.get("core_cpi", []))
    snapshot_history = {
        "cpi_yoy": cpi_yoy_history,
        "core_cpi_yoy": core_cpi_yoy_history,
    }

    return InflationSnapshot(
        # Level / YoY series from FRED
        cpi_yoy=ind.cpi_yoy,
        core_cpi_yoy=ind.core_cpi_yoy,
        ppi_yoy=ind.ppi_yoy,
        pce_yoy=ind.pce_yoy,
        sticky_cpi_yoy=ind.sticky_cpi_yoy,
        trimmed_mean_pce_yoy=ind.trimmed_mean_pce_yoy,
        # Market expectations from FRED
        breakeven_5y=ind.breakeven_5y,
        five_y_five_y_forward=ind.five_y_five_y_forward,
        treasury_10y=ind.treasury_10y,
        wti_oil_price=ind.wti_oil_price,
        brent_oil_price=ind.brent_oil_price,
        gasoline_price=ind.gasoline_price,
        copper_price=ind.copper_price,
        commodity_composite=ind.commodity_composite,
        cpi_yoy_3m=annualised_3m(cpi_yoy_history),
        core_cpi_yoy_3m=annualised_3m(core_cpi_yoy_history),
        cpi_slope_6m=regression_slope(cpi_yoy_history, n=6),
        # History and release dates
        history=snapshot_history,
        release_dates=release_dates,
    )
