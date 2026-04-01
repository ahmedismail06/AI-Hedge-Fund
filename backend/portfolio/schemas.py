"""
Portfolio Pydantic schemas — Component 4 (Portfolio Construction & Sizing).

Three models are defined here:

  PortfolioSnapshot   — lightweight read of current exposure at a point in time;
                        embedded inside SizingRecommendation to capture the
                        post-sizing portfolio state.

  ExposureState       — richer exposure snapshot that includes regime-driven
                        limits; used by exposure_tracker.py and the portfolio
                        API router.

  SizingRecommendation — the primary output of sizing_engine.py; one record
                         per sizing decision, persisted to the `positions`
                         Supabase table with status PENDING_APPROVAL.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# PortfolioSnapshot
# ---------------------------------------------------------------------------


class PortfolioSnapshot(BaseModel):
    """Lightweight snapshot of portfolio exposure at a single point in time.

    Embedded inside SizingRecommendation as `portfolio_state_after` so that
    every sizing record carries a self-contained picture of what the portfolio
    looks like if the trade is approved.
    """

    gross_exposure_pct: float = Field(
        description="Total long + short exposure as a percentage of portfolio NAV."
    )
    net_exposure_pct: float = Field(
        description="(Long − Short) exposure as a percentage of portfolio NAV."
    )
    sector_concentration: dict[str, float] = Field(
        description=(
            "Sector ticker → weight mapping, e.g. {'Healthcare': 0.18}. "
            "Values are fractions of portfolio NAV (0–1)."
        )
    )
    position_count: int = Field(
        description="Number of open positions (long + short) after this trade."
    )


# ---------------------------------------------------------------------------
# ExposureState
# ---------------------------------------------------------------------------


class ExposureState(BaseModel):
    """Full exposure state including regime-driven limits.

    Produced by exposure_tracker.py and consumed by the portfolio agent and
    API router.  The `max_*` fields reflect the hard caps for the current
    macro regime (Risk-On: 150% gross / 100% net long; Risk-Off: 80% gross /
    60% net long — see domain rules).
    """

    gross_exposure_pct: float = Field(
        description="Total long + short exposure as a percentage of portfolio NAV."
    )
    net_exposure_pct: float = Field(
        description="(Long − Short) exposure as a percentage of portfolio NAV."
    )
    max_gross_pct: float = Field(
        description="Regime-determined hard cap on gross exposure (e.g. 150 for Risk-On)."
    )
    max_net_long_pct: float = Field(
        description="Regime-determined hard cap on net long exposure."
    )
    max_net_short_pct: float = Field(
        description="Regime-determined hard cap on net short exposure (expressed as a positive number)."
    )
    sector_concentration: dict[str, float] = Field(
        description=(
            "Sector name → weight mapping. Values are fractions of portfolio NAV (0–1)."
        )
    )
    position_count: int = Field(
        description="Total number of open positions (long + short)."
    )
    regime: Literal["Risk-On", "Risk-Off", "Transitional", "Stagflation"] = Field(
        description="Active macro regime at the time this state was computed."
    )


# ---------------------------------------------------------------------------
# SizingRecommendation
# ---------------------------------------------------------------------------


class SizingRecommendation(BaseModel):
    """Output of sizing_engine.py — one record per Kelly sizing decision.

    Persisted to the `positions` Supabase table.  The record is created with
    `status = 'PENDING_APPROVAL'` and transitions to APPROVED / REJECTED when
    a human (or autonomous mode) acts on it.

    Size labels map to portfolio weight targets:
        large  → 8 %
        medium → 5 %
        small  → 2 %
        micro  → 1 %

    These are the *target* weights; `pct_of_portfolio` reflects the actual
    Kelly-computed weight (which may be clipped by the 15 % hard position cap).
    """

    ticker: str = Field(min_length=1, description="Exchange ticker symbol, e.g. 'AAPL'.")
    date: str = Field(description="ISO date (YYYY-MM-DD) when sizing was computed.")
    direction: Literal["LONG", "SHORT"] = Field(
        description="Trade direction — Phase 1 is long-only; SHORT unlocked at Phase 2."
    )
    conviction_score: float = Field(
        ge=0.0,
        le=10.0,
        description=(
            "Conviction score from the InvestmentMemo (0–10). "
            "Used as win-rate proxy in Kelly formula until trade history accumulates."
        ),
    )

    # ── Dollar sizing ────────────────────────────────────────────────────────
    dollar_size: float = Field(
        description="Position size in dollars, post-Kelly and post-cap."
    )
    share_count: float = Field(
        description="Number of shares: dollar_size / entry_price."
    )
    size_label: Literal["large", "medium", "small", "micro"] = Field(
        description=(
            "Discrete size label derived from pct_of_portfolio: "
            "large ≥ 6%, medium ≥ 3.5%, small ≥ 1.5%, micro otherwise."
        )
    )
    pct_of_portfolio: float = Field(
        description=(
            "Actual weight of this position as a fraction of portfolio NAV (0–1). "
            "Hard cap is 0.15 (15%)."
        )
    )

    # ── Prices ───────────────────────────────────────────────────────────────
    entry_price: float = Field(
        description="Reference entry price used for share_count and stop calculation."
    )
    stop_loss_price: float = Field(
        description=(
            "Tier 1 stop price: entry * (1 − 0.08) for LONG in normal regime; "
            "entry * (1 − 0.05) in Risk-Off."
        )
    )
    target_price: Optional[float] = Field(
        default=None,
        description="Price target from InvestmentMemo.price_target; null if not available.",
    )
    risk_reward_ratio: Optional[float] = Field(
        default=None,
        description=(
            "(target_price − entry_price) / (entry_price − stop_loss_price). "
            "Null when target_price is unavailable."
        ),
    )

    # ── Rationale & flags ────────────────────────────────────────────────────
    sizing_rationale: str = Field(
        description=(
            "One-paragraph explanation of the Kelly inputs, any cap overrides, "
            "and regime adjustments applied."
        )
    )
    correlation_flag: bool = Field(
        description=(
            "True if this ticker has 60-day rolling correlation > 0.7 with an "
            "existing position; triggers a size reduction."
        )
    )
    correlation_note: Optional[str] = Field(
        default=None,
        description="Human-readable note when correlation_flag is True, e.g. 'High corr with MSFT (0.82)'.",
    )

    # ── Context ──────────────────────────────────────────────────────────────
    sector: Optional[str] = Field(
        default=None,
        description="GICS sector of the ticker, e.g. 'Healthcare'. Used for concentration checks.",
    )
    regime_at_sizing: Literal["Risk-On", "Risk-Off", "Transitional", "Stagflation"] = Field(
        description="Macro regime active when this sizing was computed."
    )
    portfolio_state_after: PortfolioSnapshot = Field(
        description=(
            "Projected portfolio snapshot assuming this trade is approved. "
            "Lets reviewers see concentration and exposure impact before approving."
        )
    )

    # ── Lifecycle ────────────────────────────────────────────────────────────
    status: Literal["PENDING_APPROVAL", "APPROVED", "REJECTED", "OPEN", "CLOSED"] = Field(
        default="PENDING_APPROVAL",
        description=(
            "Approval status.  Starts as PENDING_APPROVAL; "
            "transitions to APPROVED or REJECTED via the dashboard or autonomous mode; "
            "OPEN once the order is filled; CLOSED when exited."
        ),
    )
