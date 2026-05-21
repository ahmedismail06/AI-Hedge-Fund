"""Market-expectations inflation layer.

PR 3 of the inflation engine redesign (docs/inflation_engine_design.md §2.1, §2.4).

This layer captures *forward-looking* inflation signals from market prices.
Market-based indicators (breakevens, commodity prices, yield forwards) update
daily, giving the engine real-time sensitivity between monthly CPI releases.

Indicators:
    breakeven_5y          -- 5Y breakeven (FRED T5YIE); center=2.25, scale=0.5
    five_y_five_y_forward -- 5Y5Y forward inflation (FRED T5YIFR); center=2.30, scale=0.5
    wti_oil_price         -- USO ETF; scored via rolling_zscore against history
    gasoline_price        -- UGA ETF; scored via rolling_zscore against history
    copper_price          -- CPER ETF; scored via rolling_zscore against history
    commodity_composite   -- DBC ETF; scored via rolling_zscore against history

Normalisation for commodity prices:
    - Absolute price levels are not inflation signals — only changes relative
      to recent history matter.
    - We use ``rolling_zscore`` (or ``percentile_rank`` when history is short)
      from normalize.py so that a commodity at its 3-month high scores near +1
      and at its 3-month low scores near -1.
    - History is supplied via snapshot.history[key] if available; falls back to
      a tanh_norm with generous economic anchors if history is absent.

Commodity fallback anchors (used when history is unavailable):
    USO/WTI:    center=70, scale=25  (USO price around $70-75 is "normal";
                ±$25 ≈ one standard deviation over recent years)
    UGA/gas:    center=55, scale=15
    CPER/copper: center=24, scale=8
    DBC:        center=22, scale=6

Breakeven indicators are scored via tanh_norm — they have stable economic
anchors (Fed target) so level-based scoring makes sense.

Confidence:
    Full confidence (1.0) when all 6 indicators are present.
    Degrades proportionally for each missing input.
    Breakevens count double (more economically precise than commodity ETFs).
"""

from __future__ import annotations

import logging
from typing import Optional

from backend.macro.inflation_engine import config as cfg
from backend.macro.inflation_engine.normalize import rolling_zscore, tanh_norm
from backend.macro.inflation_engine.staleness import staleness_confidence
from backend.macro.inflation_engine.types import (
    Contribution,
    InflationSnapshot,
    LayerOutput,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Indicator specs
# ---------------------------------------------------------------------------

# (key, base_weight, tanh_params_or_None)
# tanh_params = use tanh_norm; None = use rolling_zscore against history.
_BREAKEVEN_SPECS: list[tuple[str, float, cfg.TanhParams]] = [
    ("breakeven_5y",         0.35, cfg.BREAKEVEN_5Y),
    ("five_y_five_y_forward", 0.25, cfg.FIVE_Y_FIVE_Y_FWD),
]

_COMMODITY_SPECS: list[tuple[str, float, cfg.TanhParams]] = [
    # Fallback tanh anchors for when no rolling history is available.
    ("wti_oil_price",       0.15, cfg.TanhParams(center=70.0, scale=25.0)),
    ("gasoline_price",      0.10, cfg.TanhParams(center=55.0, scale=15.0)),
    ("copper_price",        0.10, cfg.TanhParams(center=24.0, scale=8.0)),
    ("commodity_composite", 0.05, cfg.TanhParams(center=22.0, scale=6.0)),
]

_ALL_WEIGHTS = sum(w for _, w, _ in _BREAKEVEN_SPECS) + sum(w for _, w, _ in _COMMODITY_SPECS)
# Sanity check: weights should sum to 1.0
assert abs(_ALL_WEIGHTS - 1.0) < 1e-9, f"market_expectations weights sum to {_ALL_WEIGHTS}, not 1.0"


def compute(snapshot: InflationSnapshot) -> LayerOutput:
    """Compute the market-expectations inflation layer score.

    Parameters
    ----------
    snapshot:
        InflationSnapshot for the current scoring run.

    Returns
    -------
    LayerOutput
        ``score`` in [-1, +1], ``confidence`` proportional to data availability.
    """
    contributors: list[Contribution] = []
    notes: list[str] = []

    # (signal, base_weight, confidence)
    scored: list[tuple[float, float, float]] = []

    # ── Breakeven indicators — level-based tanh_norm ──────────────────────────
    for key, base_weight, params in _BREAKEVEN_SPECS:
        value: Optional[float] = getattr(snapshot, key, None)
        if value is None:
            notes.append(f"market_expectations: {key} missing — skipped")
            logger.debug("market_expectations/%s: missing", key)
            continue

        signal = tanh_norm(value, params.center, params.scale)

        # Freshness confidence: breakevens are daily — use 7-day halflife.
        from datetime import datetime
        release_dt = snapshot.release_dates.get(key)
        if release_dt is not None:
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            staleness = max(0, (today - release_dt).days)
            conf = staleness_confidence(staleness, "daily")
        else:
            conf = 1.0  # assume fresh if no date given

        contrib = Contribution(
            indicator=key,
            raw_value=value,
            signal=signal,
            weight=base_weight,
            confidence=conf,
            age_days=None,
        )
        contributors.append(contrib)
        scored.append((signal, base_weight, conf))

        logger.debug(
            "market_expectations/%s=%.4f → signal=%.4f  weight=%.2f  conf=%.3f",
            key, value, signal, base_weight, conf,
        )

    # ── Commodity indicators — rolling_zscore when history available, else tanh ─
    for key, base_weight, fallback_params in _COMMODITY_SPECS:
        value = getattr(snapshot, key, None)
        if value is None:
            notes.append(f"market_expectations: {key} missing — skipped")
            logger.debug("market_expectations/%s: missing", key)
            continue

        # Attempt rolling_zscore against historical context.
        history = snapshot.history.get(key, [])
        signal: Optional[float] = None

        if len(history) >= 8:
            signal = rolling_zscore(value, history)
            if signal is not None:
                notes.append(
                    f"market_expectations: {key} scored via rolling_zscore "
                    f"(n={len(history)} history points)"
                )
                logger.debug(
                    "market_expectations/%s=%.4f → rolling_zscore=%.4f  history_n=%d",
                    key, value, signal, len(history),
                )

        if signal is None:
            # Fallback: use tanh_norm with pre-calibrated economic anchor.
            signal = tanh_norm(value, fallback_params.center, fallback_params.scale)
            notes.append(
                f"market_expectations: {key} using tanh_norm fallback "
                f"(center={fallback_params.center}, scale={fallback_params.scale}) "
                f"— no/insufficient history"
            )
            logger.debug(
                "market_expectations/%s=%.4f → tanh_norm_fallback=%.4f "
                "(center=%.1f scale=%.1f)",
                key, value, signal, fallback_params.center, fallback_params.scale,
            )

        # Commodity ETF prices update daily.
        conf = 1.0  # assume fresh unless release_date is supplied
        release_dt = snapshot.release_dates.get(key)
        if release_dt is not None:
            from datetime import datetime
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            staleness = max(0, (today - release_dt).days)
            conf = staleness_confidence(staleness, "daily")

        contrib = Contribution(
            indicator=key,
            raw_value=value,
            signal=signal,
            weight=base_weight,
            confidence=conf,
            age_days=None,
        )
        contributors.append(contrib)
        scored.append((signal, base_weight, conf))

        logger.debug(
            "market_expectations/%s=%.4f → signal=%.4f  weight=%.2f  conf=%.3f",
            key, value, signal, base_weight, conf,
        )

    if not scored:
        logger.debug(
            "market_expectations: no indicators available → score=0.0, confidence=0.0"
        )
        return LayerOutput(
            score=0.0,
            confidence=0.0,
            contributors=[],
            notes=notes + ["market_expectations: no indicators available"],
        )

    # Effective weight = base_weight × confidence; renormalise.
    effective_weights = [bw * c for _, bw, c in scored]
    total_eff = sum(effective_weights)

    # Total base weight across ALL possible indicators (present or absent).
    total_possible_bw = sum(bw for _, bw, _ in _BREAKEVEN_SPECS) + sum(bw for _, bw, _ in _COMMODITY_SPECS)

    if total_eff == 0.0:
        score = sum(s for s, _, _ in scored) / len(scored)
        layer_confidence = 0.0
        notes.append("market_expectations: all confidences zero — unweighted mean fallback")
    else:
        score = sum(s * ew for (s, _, _), ew in zip(scored, effective_weights)) / total_eff
        # Layer confidence = (sum of present indicators' base_weight × freshness_confidence)
        # divided by total possible base weight.  This penalises both staleness AND
        # missing coverage — a layer with only one breakeven present scores < 1.0
        # even when that breakeven is perfectly fresh.
        layer_confidence = (
            sum(c * bw for _, bw, c in scored) / total_possible_bw
        )

    score = max(-1.0, min(1.0, score))

    logger.debug(
        "market_expectations → score=%.4f  confidence=%.4f  n_indicators=%d",
        score, layer_confidence, len(scored),
    )

    return LayerOutput(
        score=score,
        confidence=layer_confidence,
        contributors=contributors,
        notes=notes,
    )
