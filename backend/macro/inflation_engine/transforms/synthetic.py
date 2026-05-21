"""Synthetic indicator transforms for the inflation scoring engine.

PR 3 of the inflation engine redesign (docs/inflation_engine_design.md §3).

Synthetic transforms combine two or more raw inputs to produce a derived
indicator that better captures underlying inflation dynamics than either
input alone.

All functions are pure: no I/O, no globals.

Functions
---------
dxy_adjusted_oil:  Remove DXY-driven component from oil price moves.
"""

from __future__ import annotations


__all__ = ["dxy_adjusted_oil"]


def dxy_adjusted_oil(
    oil_price: float,
    dxy: float,
    baseline_dxy: float = 103.0,
) -> float:
    """Oil price adjusted for US dollar strength.

    Since oil is priced in USD, a stronger dollar mechanically depresses oil
    prices in USD terms even when underlying supply/demand conditions are
    unchanged. This transform isolates the "real" oil inflation signal by
    removing the dollar component.

    Formula::

        dxy_factor = baseline_dxy / dxy
        adjusted_oil = oil_price * dxy_factor

    Interpretation:
    - If DXY is at baseline, adjusted_oil == oil_price (no adjustment).
    - If DXY is above baseline (stronger dollar), adjusted_oil > oil_price
      (dollar has masked some of the real oil price increase).
    - If DXY is below baseline (weaker dollar), adjusted_oil < oil_price
      (some of the oil price rise is just dollar weakness, not real demand).

    Calibration:
        ``baseline_dxy = 103.0`` reflects the approximate DXY level in early
        2024–2026, the period most relevant for current-regime scoring.
        This can be updated in config.py as the dollar regime shifts.

    Parameters
    ----------
    oil_price:
        Current oil proxy price (e.g. USO ETF close).
    dxy:
        Current DXY level (e.g. 103.5).
    baseline_dxy:
        The reference DXY level at which no adjustment is applied.
        Default 103.0.

    Returns
    -------
    float
        DXY-adjusted oil price in the same units as ``oil_price``.

    Raises
    ------
    ValueError
        If ``dxy`` is zero or negative (undefined adjustment).
    """
    if dxy <= 0:
        raise ValueError(f"dxy_adjusted_oil: dxy must be > 0, got {dxy}")
    dxy_factor = baseline_dxy / dxy
    return oil_price * dxy_factor
