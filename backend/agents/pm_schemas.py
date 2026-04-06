"""
Pydantic schemas for the AI Portfolio Manager Agent (Component 8 v2).

Models cover:
  PMConfig            — runtime configuration (mode, halt state)
  PMContextSnapshot   — portfolio state captured at decision time
  PMDecision          — one PM reasoning decision (entry, exit, rebalance, crisis, earnings)
  PMCycleStatus       — summary of one 5-minute PM cycle
  HumanOverride       — human intervention record attached to a decision
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class PMConfig(BaseModel):
    mode: Literal["autonomous", "supervised"] = Field(
        default="autonomous",
        description="autonomous = PM acts without approval; supervised = decisions wait for human click",
    )
    cycle_interval_seconds: int = Field(
        default=300,
        description="How often the PM decision cycle runs (seconds)",
    )
    daily_loss_halt_triggered: bool = Field(
        default=False,
        description="True when intraday drawdown has exceeded -10% — all trading halted for the day",
    )
    halted_until: Optional[datetime] = Field(
        default=None,
        description="ISO UTC timestamp when the halt expires (None = no active halt)",
    )


class PMContextSnapshot(BaseModel):
    gross_exposure: float = Field(
        description="Sum of absolute position weights as a fraction of portfolio (e.g. 0.85 = 85%)",
    )
    net_exposure: float = Field(
        description="Long weight minus short weight (e.g. 0.42 = net long 42%)",
    )
    position_count: int = Field(
        description="Number of OPEN positions",
    )
    cash_pct: float = Field(
        description="Cash as a fraction of portfolio (1 - gross_exposure, approximate)",
    )
    macro_regime: str = Field(
        description="Current macro regime: Risk-On | Risk-Off | Transitional | Stagflation",
    )
    active_critical_alerts: int = Field(
        description="Count of unresolved CRITICAL risk alerts",
    )


class HumanOverride(BaseModel):
    override_type: Literal["BLOCK", "MODIFY", "FORCE_EXECUTE", "HALT", "RESUME"] = Field(
        description="Type of human intervention",
    )
    reason: str = Field(
        description="Human-provided rationale for the override",
    )
    original_decision_id: Optional[str] = Field(
        default=None,
        description="Decision ID that was overridden (null for portfolio-level halts/resumes)",
    )
    timestamp: datetime = Field(
        description="UTC timestamp of the override action",
    )


class PMDecision(BaseModel):
    decision_id: str = Field(
        description="Unique identifier: pm_YYYYMMDD_NNN",
    )
    timestamp: datetime = Field(
        description="UTC timestamp when this decision was made",
    )
    category: Literal["NEW_ENTRY", "EXIT_TRIM", "REBALANCE", "CRISIS", "PRE_EARNINGS"] = Field(
        description="Decision category determines which prompt template was used",
    )
    ticker: Optional[str] = Field(
        default=None,
        description="Ticker symbol (null for portfolio-level decisions like REBALANCE with NO_ACTION)",
    )
    decision: str = Field(
        description=(
            "PM's chosen action. Per category: "
            "NEW_ENTRY → EXECUTE|MODIFY_SIZE|DEFER|REJECT|WATCHLIST; "
            "EXIT_TRIM → HOLD|TRIM|CLOSE|ADD; "
            "REBALANCE → NO_ACTION|REBALANCE|RAISE_CASH|DEPLOY_CASH; "
            "CRISIS → REDUCE_EXPOSURE|HALT_NEW_ENTRIES|LIQUIDATE_TO_TARGET|HEDGE|MONITOR; "
            "PRE_EARNINGS → SIZE_UP|HOLD|TRIM|EXIT"
        ),
    )
    action_details: Dict[str, Any] = Field(
        default_factory=dict,
        description="Category-specific action parameters (shares, dollar_amount, trim_pct, target_exposure, etc.)",
    )
    reasoning: str = Field(
        description="Claude's 2-3 sentence explanation of the key factors driving this decision",
    )
    risk_assessment: str = Field(
        description="What could go wrong with this decision",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="PM's confidence in this decision (0.0–1.0); tracked against outcomes for calibration",
    )
    context_snapshot: PMContextSnapshot = Field(
        description="Full portfolio state at the moment this decision was made",
    )
    hard_blocks_checked: Dict[str, bool] = Field(
        default_factory=dict,
        description="Pre-Claude hard block results: position_cap_ok, market_hours_ok, gross_exposure_ok, daily_loss_ok",
    )
    execution_status: Literal["SENT_TO_EXECUTION", "BLOCKED", "DEFERRED", "NO_ACTION", "PENDING_HUMAN"] = Field(
        description=(
            "Outcome after routing: SENT_TO_EXECUTION = order queued; "
            "BLOCKED = hard block prevented execution; "
            "DEFERRED = PM chose to wait; "
            "NO_ACTION = no change needed; "
            "PENDING_HUMAN = supervised mode, waiting for Dashboard approval"
        ),
    )
    human_override: Optional[HumanOverride] = Field(
        default=None,
        description="Populated if a human intervened after this decision was made",
    )


class PMCycleStatus(BaseModel):
    cycle_id: str = Field(
        description="Unique cycle identifier: cycle_YYYYMMDD_HHMM",
    )
    timestamp: datetime = Field(
        description="UTC timestamp when the cycle started",
    )
    cycle_type: Literal["SCHEDULED", "REACTIVE_CRITICAL", "REACTIVE_REGIME", "HUMAN_OVERRIDE"] = Field(
        description="What triggered this cycle",
    )
    items_evaluated: int = Field(
        description="Number of actionable items found and evaluated this cycle",
    )
    decisions_made: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Summary list of decisions: [{decision_id, ticker, decision, category}]",
    )
    portfolio_state_after: PMContextSnapshot = Field(
        description="Portfolio state after all decisions this cycle were executed",
    )
    next_cycle: Optional[datetime] = Field(
        default=None,
        description="UTC timestamp of the next scheduled cycle",
    )
