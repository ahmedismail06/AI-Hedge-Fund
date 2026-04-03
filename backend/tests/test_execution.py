"""
Smoke tests for the Execution Agent layer.

Covers:
  - broker/schemas.py         (Pydantic model validation — no mocking needed)
  - broker/order_builder.py   (_select_order_type pure logic + build_order with mocked _fetch_adv)
  - agents/execution_agent.py (market hours guard + cycle orchestration with mocked Supabase/IBKR)
  - api/execution.py          (6 FastAPI endpoints via TestClient with mocked Supabase/broker)

Run:
    python -m pytest backend/tests/test_execution.py -v
"""

import asyncio
import os
import sys
from datetime import datetime
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Project root on sys.path so imports resolve correctly.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# ---------------------------------------------------------------------------
# Async helper (matches test_portfolio.py convention)
# ---------------------------------------------------------------------------


def _run(coro):
    """Run an async coroutine synchronously inside a test."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ===========================================================================
# Schema tests — Pydantic validation, no mocking required
# ===========================================================================

from pydantic import ValidationError
from backend.broker.schemas import ExecutionSummary, FillRecord, OrderRequest, OrderStatus


class TestSchemas:
    def test_order_request_constructs_correctly(self):
        """OrderRequest with all valid fields constructs without error."""
        req = OrderRequest(
            position_id="pos-uuid-001",
            ticker="AAPL",
            direction="LONG",
            order_type="LIMIT",
            requested_qty=100,
            limit_price=150.25,
            intended_price=150.0,
            timeout_minutes=10,
        )
        assert req.ticker == "AAPL"
        assert req.direction == "LONG"
        assert req.limit_price == 150.25

    def test_order_request_vwap_with_none_limit_price(self):
        """OrderRequest with limit_price=None and order_type='VWAP_30' is valid."""
        req = OrderRequest(
            position_id="pos-uuid-002",
            ticker="MSFT",
            direction="LONG",
            order_type="VWAP_30",
            requested_qty=50,
            limit_price=None,
            intended_price=300.0,
            timeout_minutes=30,
        )
        assert req.limit_price is None
        assert req.order_type == "VWAP_30"

    def test_order_status_defaults(self):
        """OrderStatus fields total_filled_qty and avg_fill_price have correct defaults."""
        status = OrderStatus(
            order_id="order-uuid-001",
            status="PENDING",
        )
        assert status.total_filled_qty == 0.0
        assert status.avg_fill_price is None
        assert status.submitted_at is None
        assert status.filled_at is None

    def test_execution_summary_defaults(self):
        """ExecutionSummary defaults: approved_found=0, critical_blocked=False, etc."""
        summary = ExecutionSummary(cycle_at="2026-04-01T10:00:00")
        assert summary.approved_found == 0
        assert summary.critical_blocked is False
        assert summary.skipped_market_closed is False
        assert summary.position_ids_filled == []
        assert summary.orders_placed == 0
        assert summary.errors == []

    def test_fill_record_constructs_correctly(self):
        """FillRecord with all required fields constructs without error."""
        fill = FillRecord(
            order_id="order-uuid-002",
            position_id="pos-uuid-003",
            ticker="GOOG",
            fill_qty=25.0,
            fill_price=175.50,
            fill_time="2026-04-01T14:35:22",
            commission=1.25,
            exchange="NASDAQ",
            intended_price=175.00,
        )
        assert fill.ticker == "GOOG"
        assert fill.fill_qty == 25.0
        assert fill.commission == 1.25

    def test_fill_record_optional_fields_default_none(self):
        """FillRecord commission and exchange default to None when omitted."""
        fill = FillRecord(
            order_id="order-uuid-003",
            position_id="pos-uuid-004",
            ticker="XYZ",
            fill_qty=10.0,
            fill_price=50.0,
            fill_time="2026-04-01T15:00:00",
            intended_price=49.90,
        )
        assert fill.commission is None
        assert fill.exchange is None


# ===========================================================================
# order_builder tests — _select_order_type is pure logic; build_order mocks
# _fetch_adv to avoid network calls.
# ===========================================================================

from backend.broker.order_builder import OrderBuildError, _select_order_type, build_order


class TestOrderBuilder:
    def test_select_order_type_zero_adv_fallback(self):
        """_select_order_type(10, 0) → LIMIT with 10-min timeout (zero ADV fallback)."""
        order_type, timeout = _select_order_type(10, 0)
        assert order_type == "LIMIT"
        assert timeout == 10

    def test_select_order_type_small_ratio_limit(self):
        """50 shares / 10000 ADV = 0.5% < 1% → LIMIT, 10 min."""
        order_type, timeout = _select_order_type(50, 10000)
        assert order_type == "LIMIT"
        assert timeout == 10

    def test_select_order_type_medium_ratio_vwap30(self):
        """200 shares / 10000 ADV = 2%, within 1–5% band → VWAP_30, 30 min."""
        order_type, timeout = _select_order_type(200, 10000)
        assert order_type == "VWAP_30"
        assert timeout == 30

    def test_select_order_type_large_ratio_vwap_day(self):
        """1000 shares / 10000 ADV = 10% > 5% → VWAP_DAY, 390 min."""
        order_type, timeout = _select_order_type(1000, 10000)
        assert order_type == "VWAP_DAY"
        assert timeout == 390

    def test_build_order_raises_for_non_long_direction(self):
        """build_order() raises OrderBuildError when direction == 'SHORT'."""
        position_row = {
            "id": "pos-001",
            "ticker": "AAPL",
            "direction": "SHORT",
            "share_count": 100,
            "entry_price": 150.0,
        }
        with pytest.raises(OrderBuildError, match="long-only"):
            build_order(position_row)

    def test_build_order_raises_when_required_field_missing(self):
        """build_order() raises OrderBuildError when a required field is None."""
        position_row = {
            "id": "pos-002",
            "ticker": "MSFT",
            "direction": "LONG",
            "share_count": None,   # missing/None
            "entry_price": 300.0,
        }
        with pytest.raises(OrderBuildError, match="share_count"):
            build_order(position_row)

    def test_build_order_raises_when_ticker_missing(self):
        """build_order() raises OrderBuildError when ticker is None."""
        position_row = {
            "id": "pos-003",
            "ticker": None,
            "direction": "LONG",
            "share_count": 50,
            "entry_price": 100.0,
        }
        with pytest.raises(OrderBuildError, match="ticker"):
            build_order(position_row)

    def test_build_order_returns_triple_for_valid_long(self):
        """
        build_order() with a valid LONG position row and mocked _fetch_adv → 10000
        returns a (OrderRequest, Stock, LimitOrder) triple with correct fields.
        """
        position_row = {
            "id": "pos-004",
            "ticker": "NVDA",
            "direction": "LONG",
            "share_count": 40,      # 40 / 10000 = 0.4% < 1% → LIMIT
            "entry_price": 800.0,
        }
        with patch("backend.broker.order_builder._fetch_adv", return_value=10000.0):
            req, contract, order = build_order(position_row)

        assert isinstance(req, OrderRequest)
        assert req.ticker == "NVDA"
        assert req.direction == "LONG"
        assert req.order_type == "LIMIT"
        assert req.requested_qty == 40
        assert req.timeout_minutes == 10
        assert req.limit_price is not None   # LIMIT orders carry a price
        assert req.intended_price == 800.0
        # Contract and order are ib_insync objects — verify they are not None
        assert contract is not None
        assert order is not None


# ===========================================================================
# execution_agent tests — market-hours guard + cycle orchestration
# ===========================================================================

from backend.agents.execution_agent import _is_market_open, run_execution_cycle


class TestExecutionAgent:
    # ── _is_market_open tests ──────────────────────────────────────────────

    def test_market_closed_on_sunday(self):
        """_is_market_open() returns False when called on a Sunday."""
        # datetime.weekday() == 6 for Sunday
        fake_sunday = MagicMock()
        fake_sunday.weekday.return_value = 6  # Sunday
        fake_sunday.time.return_value = datetime.strptime("10:30", "%H:%M").time()

        import pytz
        _ET = pytz.timezone("America/New_York")
        with patch("backend.agents.execution_agent.datetime") as mock_dt:
            mock_dt.now.return_value = fake_sunday
            result = _is_market_open()
        assert result is False

    def test_market_closed_before_open(self):
        """_is_market_open() returns False at 09:00 ET on a Monday."""
        fake_monday = MagicMock()
        fake_monday.weekday.return_value = 0  # Monday
        fake_monday.time.return_value = datetime.strptime("09:00", "%H:%M").time()

        with patch("backend.agents.execution_agent.datetime") as mock_dt:
            mock_dt.now.return_value = fake_monday
            result = _is_market_open()
        assert result is False

    def test_market_open_mid_morning(self):
        """_is_market_open() returns True at 10:30 ET on a Monday."""
        fake_monday = MagicMock()
        fake_monday.weekday.return_value = 0  # Monday
        fake_monday.time.return_value = datetime.strptime("10:30", "%H:%M").time()

        with patch("backend.agents.execution_agent.datetime") as mock_dt:
            mock_dt.now.return_value = fake_monday
            result = _is_market_open()
        assert result is True

    def test_market_closed_at_exactly_1600(self):
        """
        _is_market_open() returns False at 16:00 ET — upper bound is exclusive
        at 15:55, so 16:00 is clearly outside trading hours.
        """
        fake_monday = MagicMock()
        fake_monday.weekday.return_value = 0  # Monday
        fake_monday.time.return_value = datetime.strptime("16:00", "%H:%M").time()

        with patch("backend.agents.execution_agent.datetime") as mock_dt:
            mock_dt.now.return_value = fake_monday
            result = _is_market_open()
        assert result is False

    # ── run_execution_cycle tests ──────────────────────────────────────────

    def test_cycle_skipped_when_market_closed(self):
        """
        run_execution_cycle() returns skipped_market_closed=True when
        _is_market_open returns False and force=False (the default).
        """
        with patch("backend.agents.execution_agent._is_market_open", return_value=False):
            summary = _run(run_execution_cycle(force=False))
        assert summary.skipped_market_closed is True
        assert summary.critical_blocked is False

    def test_cycle_blocked_when_critical_alerts_exist(self):
        """
        run_execution_cycle() returns critical_blocked=True when
        _has_critical_alerts returns True (market is open).
        """
        with patch("backend.agents.execution_agent._is_market_open", return_value=True):
            with patch("backend.agents.execution_agent._has_critical_alerts", return_value=True):
                summary = _run(run_execution_cycle())
        assert summary.critical_blocked is True
        assert summary.skipped_market_closed is False

    def test_cycle_approved_found_zero_when_no_approved_positions(self):
        """
        run_execution_cycle() returns approved_found=0 when Supabase returns
        an empty list for the APPROVED positions query.
        """
        # Build a mock Supabase client that returns empty approved positions.
        mock_client = MagicMock()
        empty_result = MagicMock()
        empty_result.data = []

        def table_side_effect(table_name: str):
            tbl = MagicMock()
            tbl.select.return_value = tbl
            tbl.eq.return_value = tbl
            tbl.in_.return_value = tbl
            tbl.lt.return_value = tbl
            tbl.order.return_value = tbl
            tbl.limit.return_value = tbl
            tbl.execute.return_value = empty_result
            return tbl

        mock_client.table.side_effect = table_side_effect

        with patch("backend.agents.execution_agent._is_market_open", return_value=True):
            with patch("backend.agents.execution_agent._has_critical_alerts", return_value=False):
                with patch("backend.agents.execution_agent._get_client", return_value=mock_client):
                    with patch("backend.broker.order_manager._get_client", return_value=mock_client):
                        with patch("backend.broker.ibkr.disconnect"):
                            summary = _run(run_execution_cycle())

        assert summary.approved_found == 0
        assert summary.orders_placed == 0

    def test_cycle_force_bypasses_market_hours(self):
        """
        run_execution_cycle(force=True) bypasses the market-hours guard and
        does NOT set skipped_market_closed=True, even when the market is closed.
        """
        mock_client = MagicMock()
        empty_result = MagicMock()
        empty_result.data = []

        def table_side_effect(table_name: str):
            tbl = MagicMock()
            tbl.select.return_value = tbl
            tbl.eq.return_value = tbl
            tbl.in_.return_value = tbl
            tbl.lt.return_value = tbl
            tbl.order.return_value = tbl
            tbl.limit.return_value = tbl
            tbl.execute.return_value = empty_result
            return tbl

        mock_client.table.side_effect = table_side_effect

        # _is_market_open returns False, but force=True should bypass it.
        with patch("backend.agents.execution_agent._is_market_open", return_value=False):
            with patch("backend.agents.execution_agent._has_critical_alerts", return_value=False):
                with patch("backend.agents.execution_agent._get_client", return_value=mock_client):
                    with patch("backend.broker.order_manager._get_client", return_value=mock_client):
                        with patch("backend.broker.ibkr.disconnect"):
                            summary = _run(run_execution_cycle(force=True))

        assert summary.skipped_market_closed is False


# ===========================================================================
# API endpoint tests — FastAPI TestClient with mocked Supabase + broker
# ===========================================================================

from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)


def _make_mock_supabase_api(
    orders: Optional[list] = None,
    fills: Optional[list] = None,
    active_count: int = 0,
):
    """
    Build a mock Supabase client for API-layer tests.
    Tables: orders, fills.  active_count used for the count= query.
    """
    mock = MagicMock()

    def table_side_effect(table_name: str):
        tbl = MagicMock()
        tbl.select.return_value = tbl
        tbl.eq.return_value = tbl
        tbl.in_.return_value = tbl
        tbl.order.return_value = tbl
        tbl.limit.return_value = tbl
        tbl.update.return_value = tbl

        if table_name == "orders":
            result = MagicMock()
            result.data = orders if orders is not None else []
            result.count = active_count
            tbl.execute.return_value = result

        elif table_name == "fills":
            result = MagicMock()
            result.data = fills if fills is not None else []
            tbl.execute.return_value = result

        return tbl

    mock.table.side_effect = table_side_effect
    return mock


class TestExecutionAPI:
    def test_get_status_returns_expected_keys(self):
        """GET /execution/status returns ibkr_connected, is_paper, active_orders."""
        mock_supabase = _make_mock_supabase_api(active_count=3)

        with patch("backend.api.execution._get_client", return_value=mock_supabase):
            # Ensure _ib is None so ibkr_connected defaults to False
            with patch("backend.broker.ibkr._ib", None):
                resp = client.get("/execution/status")

        assert resp.status_code == 200
        data = resp.json()
        assert "ibkr_connected" in data
        assert "is_paper" in data
        assert "active_orders" in data
        assert isinstance(data["ibkr_connected"], bool)
        assert isinstance(data["is_paper"], bool)
        assert isinstance(data["active_orders"], int)

    def test_list_orders_returns_200_with_list(self):
        """GET /execution/orders returns 200 with a list (possibly empty)."""
        mock_order = {
            "id": "order-001",
            "ticker": "AAPL",
            "status": "SUBMITTED",
            "direction": "LONG",
        }
        mock_supabase = _make_mock_supabase_api(orders=[mock_order])

        with patch("backend.api.execution._get_client", return_value=mock_supabase):
            resp = client.get("/execution/orders")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert data[0]["ticker"] == "AAPL"

    def test_list_orders_passes_status_filter(self):
        """
        GET /execution/orders?status=FILLED passes the status filter — the
        endpoint calls .eq("status", "FILLED") on the orders query builder.
        We capture the shared table mock via side_effect so we can inspect it
        after the request completes.
        """
        # Shared mock that every call to mock_supabase.table("orders") returns.
        shared_tbl = MagicMock()
        shared_tbl.select.return_value = shared_tbl
        shared_tbl.eq.return_value = shared_tbl
        shared_tbl.order.return_value = shared_tbl
        shared_tbl.limit.return_value = shared_tbl
        result_mock = MagicMock()
        result_mock.data = []
        shared_tbl.execute.return_value = result_mock

        mock_supabase = MagicMock()
        mock_supabase.table.return_value = shared_tbl

        with patch("backend.api.execution._get_client", return_value=mock_supabase):
            resp = client.get("/execution/orders?status=FILLED")

        assert resp.status_code == 200
        # eq must have been called with ("status", "FILLED") at some point
        eq_calls = [str(c) for c in shared_tbl.eq.call_args_list]
        assert any("FILLED" in call for call in eq_calls), (
            f".eq() was not called with 'FILLED'. Calls: {eq_calls}"
        )

    def test_get_order_returns_404_when_not_found(self):
        """GET /execution/orders/{order_id} returns 404 when order doesn't exist."""
        mock_supabase = _make_mock_supabase_api(orders=[], fills=[])

        with patch("backend.api.execution._get_client", return_value=mock_supabase):
            resp = client.get("/execution/orders/nonexistent-id")

        assert resp.status_code == 404

    def test_list_fills_returns_200_with_list(self):
        """GET /execution/fills returns 200 with list of fill records."""
        mock_fill = {
            "id": "fill-001",
            "order_id": "order-001",
            "ticker": "NVDA",
            "fill_qty": 10.0,
            "fill_price": 800.0,
        }
        mock_supabase = _make_mock_supabase_api(fills=[mock_fill])

        with patch("backend.api.execution._get_client", return_value=mock_supabase):
            resp = client.get("/execution/fills")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert data[0]["ticker"] == "NVDA"

    def test_cancel_order_returns_409_when_terminal(self):
        """
        POST /execution/cancel/{order_id} returns 409 when cancel_order returns
        False (order already in a terminal state).
        """
        with patch("backend.api.execution.cancel_order", return_value=False) as mock_cancel:
            # Patch the import alias used inside the endpoint
            with patch(
                "backend.broker.order_manager.cancel_order", return_value=False
            ):
                resp = client.post("/execution/cancel/order-terminal-001")

        assert resp.status_code == 409

    def test_cancel_order_returns_cancelled_true_on_success(self):
        """
        POST /execution/cancel/{order_id} returns {"cancelled": True, "order_id": ...}
        when cancel_order returns True.
        """
        with patch("backend.broker.order_manager.cancel_order", return_value=True):
            resp = client.post("/execution/cancel/order-live-001")

        assert resp.status_code == 200
        data = resp.json()
        assert data["cancelled"] is True
        assert data["order_id"] == "order-live-001"
