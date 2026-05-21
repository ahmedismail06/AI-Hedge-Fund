"""Thin wrapper that builds an InflationSnapshot from a RawIndicators snapshot.

PR 3 of the inflation engine redesign (docs/inflation_engine_design.md §3).

This module is deliberately thin — it just maps field names from RawIndicators
(the scorer.py dataclass) into the InflationSnapshot dataclass that the layer
modules consume.  No computation is performed here.

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


def build_snapshot_from_raw(ind: "RawIndicators") -> InflationSnapshot:
    """Build an InflationSnapshot from a RawIndicators instance.

    Fields not present in RawIndicators (e.g. sticky_cpi_yoy, wti_oil_price,
    momentum series) are left as None.  Layers degrade confidence proportionally
    when inputs are absent.

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

    return InflationSnapshot(
        # Level / YoY series from FRED
        cpi_yoy=ind.cpi_yoy,
        core_cpi_yoy=ind.core_cpi_yoy,
        ppi_yoy=ind.ppi_yoy,
        pce_yoy=ind.pce_yoy,
        # sticky_cpi_yoy / trimmed_mean_pce_yoy: not in RawIndicators yet.
        # Will be threaded through in PR4 when fred_fetcher adds those series.
        sticky_cpi_yoy=None,
        trimmed_mean_pce_yoy=None,
        # Market expectations from FRED
        breakeven_5y=ind.breakeven_5y,
        # five_y_five_y_forward: not in RawIndicators yet (PR4).
        five_y_five_y_forward=None,
        # treasury_10y not in RawIndicators directly (yield_curve_spread uses it
        # but the raw value isn't exposed). PR4 can add it.
        treasury_10y=None,
        # Commodity prices: not in RawIndicators — populated via Polygon ingestion
        # and merged externally before calling the engine.  Left None here.
        wti_oil_price=None,
        brent_oil_price=None,
        gasoline_price=None,
        copper_price=None,
        commodity_composite=None,
        # Momentum series: computed by transforms.momentum before scoring.
        # Not available from RawIndicators directly.
        cpi_yoy_3m=None,
        core_cpi_yoy_3m=None,
        cpi_slope_6m=None,
        # History and release dates
        history={},
        release_dates=release_dates,
    )
