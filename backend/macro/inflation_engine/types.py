"""Types for the inflation engine's mixed-frequency handling and layered architecture.

PR 2 (IndicatorMeta, FREQUENCY_DAYS): staleness-based confidence weighting.
PR 3 (Contribution, LayerOutput, InflationSnapshot, InflationScoreResult):
    types for the four-layer scoring architecture.

Design reference: docs/inflation_engine_design.md §2.1, §2.3, §2.7
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Protocol, runtime_checkable


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


# ---------------------------------------------------------------------------
# PR 4 — Consensus / Surprise data types
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
# PR 3 — Layer architecture types
# ---------------------------------------------------------------------------


@dataclass
class Contribution:
    """Per-indicator decomposition for one layer's score.

    Carries both the raw observation and the score contribution so that
    every final score is fully attributable to its inputs.
    """

    indicator: str
    """Series name, e.g. ``"cpi_yoy"``."""

    raw_value: float
    """The underlying observation before any normalisation."""

    signal: float
    """tanh-normed signal in [-1, +1]."""

    weight: float
    """This layer's base weight for the indicator (sums to 1.0 across a layer)."""

    confidence: float
    """Freshness confidence in [0, 1]."""

    age_days: Optional[int] = None
    """Days since last release. None if release date not available."""


@dataclass
class LayerOutput:
    """Output of a single scoring layer.

    Each layer is a pure function returning a ``LayerOutput``.  The
    ``score`` field is the layer's view of inflation pressure, the
    ``confidence`` field shrinks when inputs are stale, sparse, or
    contradictory, and the ``contributors`` list allows full attribution.
    """

    score: float
    """Layer score in [-1, +1]."""

    confidence: float
    """Aggregate confidence for this layer in [0, 1].

    Used by the aggregator to scale the layer's effective weight.
    ``confidence = 0`` signals the layer has no usable data and the
    aggregator will drop it and redistribute weight to other layers.
    """

    contributors: List[Contribution]
    """Per-indicator breakdown ordered by indicator name (or contribution magnitude)."""

    notes: List[str]
    """Human-readable diagnostics for this layer (e.g. missing inputs, fallbacks used)."""


@dataclass
class InflationSnapshot:
    """All inputs the scoring engine needs for one scoring run.

    A small pure dataclass — built by ingestion modules, consumed by layers.
    Fields are None when data is unavailable; layers degrade confidence
    proportionally so the engine still runs.

    Stays small in PR3; PR4 may add fields (consensus data).
    """

    # ── Level / YoY series (from FRED, monthly) ──────────────────────────────
    cpi_yoy: Optional[float] = None
    core_cpi_yoy: Optional[float] = None
    ppi_yoy: Optional[float] = None
    pce_yoy: Optional[float] = None
    sticky_cpi_yoy: Optional[float] = None
    """Atlanta Fed sticky-price CPI YoY (STICKCPIM157SFRBATL)."""
    trimmed_mean_pce_yoy: Optional[float] = None
    """Dallas Fed 12m trimmed-mean PCE (PCETRIM12M159SFRBDAL)."""

    # ── Market expectations (daily, from FRED + Polygon) ─────────────────────
    breakeven_5y: Optional[float] = None
    five_y_five_y_forward: Optional[float] = None
    """5-year, 5-year forward inflation rate (T5YIFR)."""
    treasury_10y: Optional[float] = None
    """10Y Treasury yield (DGS10)."""

    # ── Market commodities (from Polygon — ETF proxies) ──────────────────────
    wti_oil_price: Optional[float] = None
    """WTI crude proxy via USO ETF."""
    brent_oil_price: Optional[float] = None
    """Brent crude proxy via BNO ETF."""
    gasoline_price: Optional[float] = None
    """Gasoline proxy via UGA ETF."""
    copper_price: Optional[float] = None
    """Copper proxy via CPER ETF."""
    commodity_composite: Optional[float] = None
    """Broad commodity composite via DBC ETF."""

    # ── Momentum series (pre-computed by transforms.momentum) ────────────────
    # Layers consume these directly rather than recomputing.
    cpi_yoy_3m: Optional[float] = None
    """3m-annualised CPI momentum."""
    core_cpi_yoy_3m: Optional[float] = None
    """3m-annualised core CPI momentum."""
    cpi_slope_6m: Optional[float] = None
    """6m OLS regression slope of CPI YoY."""

    # ── Historical context for rolling normalisation ──────────────────────────
    history: Dict[str, List[float]] = field(default_factory=dict)
    """Rolling history per series key. Used by rolling_zscore and percentile_rank."""

    # ── Freshness metadata ────────────────────────────────────────────────────
    release_dates: Dict[str, datetime] = field(default_factory=dict)
    """Last release date per series key. Populated by ingestion modules."""

    # ── PR 4 — Consensus / Surprise data ──────────────────────────────────────
    expectations: List[ConsensusExpectation] = field(default_factory=list)
    """Consensus expectations for recent/upcoming releases.
    Used by the surprise layer to compute actual-vs-expected scores."""


@dataclass
class InflationScoreResult:
    """Full output of the layered inflation scoring engine.

    Returned by ``score_inflation()`` and logged at DEBUG level from
    ``_score_inflation()`` in scorer.py.  The ``score`` field is the only
    value exposed to upstream callers for backward compatibility.
    """

    score: float
    """Final inflation score in [-1, +1]."""

    confidence: float
    """Aggregate confidence across all layers in [0, 1]."""

    layers: Dict[str, LayerOutput]
    """Per-layer outputs keyed by layer name: structural, momentum,
    market_expectations, surprise."""

    contributors_top5: List[Contribution]
    """Top-5 contributors ranked by |signal × weight × confidence|."""

    aggregate_method: str
    """How layers were combined. ``"static"`` in PR3; ``"dynamic"`` added in PR4."""

    stale_warnings: List[str]
    """Human-readable staleness warnings from all layers."""

    diagnostics: Dict[str, Any]
    """Raw values, layer weights, effective weights, and per-layer confidence.
    Used for observability; surfaced via API in PR4."""
