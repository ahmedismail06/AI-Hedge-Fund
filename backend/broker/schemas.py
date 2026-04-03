# Broker Pydantic schemas — OrderRequest, OrderStatus, FillRecord, ExecutionSummary

from dotenv import load_dotenv

load_dotenv()

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# OrderRequest
# ---------------------------------------------------------------------------


class OrderRequest(BaseModel):
    """Input to order_manager.place_order(). Built by order_builder.py from an APPROVED position row."""

    position_id: str = Field(description="UUID FK to positions table.")
    ticker: str = Field(description="Exchange ticker symbol.")
    direction: Literal["LONG", "SHORT"] = Field(description="Trade direction.")
    order_type: Literal["LIMIT", "VWAP_30", "VWAP_DAY"] = Field(
        description="Execution strategy: LIMIT for small orders, VWAP_30 or VWAP_DAY for larger."
    )
    requested_qty: int = Field(description="Whole shares only — no fractional shares sent to IBKR.")
    limit_price: Optional[float] = Field(
        default=None,
        description="Limit price; None for VWAP orders.",
    )
    intended_price: float = Field(
        description=(
            "yfinance price snapshot from positions.entry_price at order creation. "
            "Carried to fills for slippage attribution."
        )
    )
    timeout_minutes: int = Field(
        description="Order timeout: 10 (LIMIT), 30 (VWAP_30), 390 (VWAP_DAY)."
    )


# ---------------------------------------------------------------------------
# OrderStatus
# ---------------------------------------------------------------------------


class OrderStatus(BaseModel):
    """Returned by order_manager methods. Drives the execution cycle poll loop."""

    order_id: str = Field(description="UUID from orders table.")
    ibkr_order_id: Optional[int] = Field(
        default=None,
        description="IBKR permId assigned after submission.",
    )
    status: Literal["PENDING", "SUBMITTED", "PARTIAL", "FILLED", "CANCELLED", "TIMEOUT", "ERROR"] = Field(
        description="Current lifecycle state of the order."
    )
    total_filled_qty: float = Field(default=0.0, description="Cumulative filled quantity so far.")
    avg_fill_price: Optional[float] = Field(
        default=None,
        description="Volume-weighted average fill price; None until at least one fill.",
    )
    submitted_at: Optional[str] = Field(
        default=None,
        description="ISO UTC timestamp when the order was submitted to IBKR.",
    )
    filled_at: Optional[str] = Field(
        default=None,
        description="ISO UTC timestamp when the order reached FILLED status.",
    )


# ---------------------------------------------------------------------------
# FillRecord
# ---------------------------------------------------------------------------


class FillRecord(BaseModel):
    """Produced by fill_recorder.handle_exec_detail() for each IBKR fill callback."""

    order_id: str = Field(description="UUID from orders table.")
    position_id: str = Field(description="UUID FK to positions table.")
    ticker: str = Field(description="Exchange ticker symbol.")
    fill_qty: float = Field(description="Number of shares filled in this execution report.")
    fill_price: float = Field(description="Price at which this partial or full fill was executed.")
    fill_time: str = Field(description="ISO UTC timestamp of the fill as reported by IBKR.")
    commission: Optional[float] = Field(
        default=None,
        description="Commission charged for this fill; None if not yet reported.",
    )
    exchange: Optional[str] = Field(
        default=None,
        description="Execution venue, e.g. 'NASDAQ', 'NYSE ARCA'.",
    )
    intended_price: float = Field(
        description=(
            "Carried forward from OrderRequest.intended_price. "
            "Slippage = fill_price − intended_price (positive = paid more than expected)."
        )
    )


# ---------------------------------------------------------------------------
# ExecutionSummary
# ---------------------------------------------------------------------------


class ExecutionSummary(BaseModel):
    """Returned by run_execution_cycle() for logging and the API."""

    cycle_at: str = Field(description="ISO UTC timestamp of cycle start.")
    approved_found: int = Field(default=0, description="Number of APPROVED positions found at cycle start.")
    orders_placed: int = Field(default=0, description="Number of orders successfully submitted to IBKR.")
    orders_filled: int = Field(default=0, description="Number of orders that reached FILLED status this cycle.")
    orders_partial: int = Field(default=0, description="Number of orders left in PARTIAL state at cycle end.")
    orders_timeout: int = Field(default=0, description="Number of orders that expired before filling.")
    orders_error: int = Field(default=0, description="Number of orders that encountered an error.")
    critical_blocked: bool = Field(
        default=False,
        description="True if the Risk Agent raised a CRITICAL flag and blocked all new orders.",
    )
    skipped_market_closed: bool = Field(
        default=False,
        description="True if the cycle exited early because the market was closed.",
    )
    position_ids_filled: List[str] = Field(
        default=[],
        description="List of position UUIDs that reached FILLED status this cycle.",
    )
    errors: List[str] = Field(
        default=[],
        description="Human-readable error messages collected during the cycle.",
    )
