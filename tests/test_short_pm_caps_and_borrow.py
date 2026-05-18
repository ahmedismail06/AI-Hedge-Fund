"""
Tests for asymmetric short-side risk controls:

  • sizing_engine — SHORT positions cap at 7.5% (half of LONG cap)
  • exposure_tracker — per-position SHORT cap (7.5%) + gross-short cap
  • borrow_check — IBKR shortable-shares gate categorization
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from backend.portfolio import sizing_engine
from backend.portfolio.exposure_tracker import (
    REGIME_CAPS,
    check_exposure_breach,
    get_current_exposure,
)
from backend.broker.borrow_check import (
    BORROW_ETB, BORROW_HTB, BORROW_UNAVAILABLE, BORROW_UNKNOWN,
    BorrowStatus, check_borrow, is_blocking,
)


# ── Sizing: asymmetric position cap ──────────────────────────────────────────
def test_short_caps_at_7_5_pct_at_high_conviction():
    """A 10/10 conviction SHORT should cap at 7.5%, not 8% tier or 15% absolute."""
    out = sizing_engine.calculate_size(
        conviction_score=10.0,
        portfolio_value=1_000_000,
        entry_price=50.0,
        regime="Risk-On",
        direction="SHORT",
    )
    # Tier-large = 8% would normally win; SHORT cap drops it to 7.5%.
    assert out["pct_of_portfolio"] == pytest.approx(0.075, abs=1e-4)


def test_long_at_same_conviction_uses_higher_cap():
    """Same conviction on LONG side: hits the 8% tier cap, not 7.5%."""
    out = sizing_engine.calculate_size(
        conviction_score=10.0,
        portfolio_value=1_000_000,
        entry_price=50.0,
        regime="Risk-On",
        direction="LONG",
    )
    # 8% tier cap binds (kelly typically higher; tier cap binds first)
    assert out["pct_of_portfolio"] <= 0.08 + 1e-6
    assert out["pct_of_portfolio"] > 0.075  # confirms LONG is wider than SHORT


def test_short_at_medium_conviction_unchanged_by_cap():
    """A medium-conviction SHORT below 7.5% is unaffected by the cap."""
    out = sizing_engine.calculate_size(
        conviction_score=7.5,
        portfolio_value=1_000_000,
        entry_price=50.0,
        regime="Risk-On",
        direction="SHORT",
    )
    # Tier-medium caps at 5%, well under 7.5%
    assert out["pct_of_portfolio"] <= 0.05 + 1e-6


# ── Exposure: per-position SHORT cap + gross-short cap ───────────────────────
def _state(**overrides) -> dict:
    base = get_current_exposure(positions=[], portfolio_value=1_000_000, regime="Risk-On")
    base = dict(base)
    base.update(overrides)
    return base


def test_short_position_above_7_5pct_breaches():
    breached, reason = check_exposure_breach(
        new_dollar_size=100_000,  # 10% on $1M
        new_direction="SHORT",
        new_sector="Tech",
        current=_state(),
        portfolio_value=1_000_000,
    )
    assert breached
    assert "SHORT" in reason and "7.5%" in reason


def test_short_at_7_5pct_does_not_breach_position_cap():
    breached, reason = check_exposure_breach(
        new_dollar_size=75_000,  # exactly 7.5%
        new_direction="SHORT",
        new_sector="Tech",
        current=_state(),
        portfolio_value=1_000_000,
    )
    # May still breach net-short floor (Risk-On is 0.0); we check the message
    # is NOT about the per-position cap.
    if breached:
        assert "per-position cap" not in reason


def test_long_at_10pct_does_not_breach_position_cap():
    """LONG at 10% (above SHORT cap but under LONG cap) should not breach the per-position cap."""
    breached, reason = check_exposure_breach(
        new_dollar_size=100_000,  # 10%
        new_direction="LONG",
        new_sector="Tech",
        current=_state(),
        portfolio_value=1_000_000,
    )
    # Could fail other gates but not the position cap
    if breached:
        assert "per-position cap" not in reason


def test_gross_short_cap_blocks_additional_short():
    """In Risk-On the gross-short cap is 50%. Block a 5th short on top of 50% already short."""
    # Build state with $500k already short on $1M NAV → gross_short_pct=0.50
    positions = [
        {"direction": "SHORT", "dollar_size": 100_000, "sector": f"S{i}"} for i in range(5)
    ]
    state = get_current_exposure(positions, 1_000_000, regime="Transitional")  # max_gross_short=0.60
    # Existing gross_short = 0.50, cap 0.60. Try to add 15% short → 65% → breach.
    breached, reason = check_exposure_breach(
        new_dollar_size=150_000,
        new_direction="SHORT",
        new_sector="X",
        current=state,
        portfolio_value=1_000_000,
    )
    assert breached
    # Could trip either per-position cap (15% > 7.5%) or gross-short cap; both are valid bears.
    assert any(token in reason for token in ("per-position cap", "gross short"))


def test_gross_short_cap_carries_regime_value():
    state = get_current_exposure(positions=[], portfolio_value=1_000_000, regime="Risk-Off")
    assert state["max_gross_short_pct"] == REGIME_CAPS["Risk-Off"]["max_gross_short"]


# ── Borrow check categorization ──────────────────────────────────────────────
def _mock_async_returning(shortable):
    """Build a mock that bypasses the ib_insync loop and returns shortable directly."""
    async def _coro(symbol, timeout):
        return shortable
    return _coro


def test_check_borrow_etb_when_shortable_far_exceeds_required():
    with patch("backend.broker.ibkr.get_loop", return_value=MagicMock(), create=True), \
         patch("backend.broker.borrow_check.asyncio.run_coroutine_threadsafe") as mock_run:
        future = MagicMock()
        future.result.return_value = 1_000_000
        mock_run.return_value = future

        status = check_borrow("AAPL", 1_000)
        assert status.status == BORROW_ETB
        assert status.shortable_shares == 1_000_000
        assert status.margin_ratio == 1000.0


def test_check_borrow_htb_when_margin_is_tight():
    with patch("backend.broker.ibkr.get_loop", return_value=MagicMock(), create=True), \
         patch("backend.broker.borrow_check.asyncio.run_coroutine_threadsafe") as mock_run:
        future = MagicMock()
        future.result.return_value = 5_000  # 5x required
        mock_run.return_value = future

        status = check_borrow("XYZ", 1_000)
        assert status.status == BORROW_HTB


def test_check_borrow_unavailable_when_zero_shares():
    with patch("backend.broker.ibkr.get_loop", return_value=MagicMock(), create=True), \
         patch("backend.broker.borrow_check.asyncio.run_coroutine_threadsafe") as mock_run:
        future = MagicMock()
        future.result.return_value = 0
        mock_run.return_value = future

        status = check_borrow("XYZ", 1_000)
        assert status.status == BORROW_UNAVAILABLE


def test_check_borrow_unavailable_when_insufficient_shares():
    with patch("backend.broker.ibkr.get_loop", return_value=MagicMock(), create=True), \
         patch("backend.broker.borrow_check.asyncio.run_coroutine_threadsafe") as mock_run:
        future = MagicMock()
        future.result.return_value = 500
        mock_run.return_value = future

        status = check_borrow("XYZ", 1_000)
        assert status.status == BORROW_UNAVAILABLE
        assert "insufficient locate" in status.reason


def test_check_borrow_unknown_when_ibkr_query_raises():
    with patch("backend.broker.ibkr.get_loop", side_effect=RuntimeError("IBKR down"), create=True):
        status = check_borrow("XYZ", 1_000)
        assert status.status == BORROW_UNKNOWN
        assert "IBKR query failed" in status.reason


# ── Blocking semantics ───────────────────────────────────────────────────────
def test_unavailable_always_blocks():
    assert is_blocking(BORROW_UNAVAILABLE) is True
    assert is_blocking(BORROW_UNAVAILABLE, allow_unknown=True) is True


def test_unknown_blocks_in_live_warns_in_paper():
    assert is_blocking(BORROW_UNKNOWN) is True
    assert is_blocking(BORROW_UNKNOWN, allow_unknown=True) is False


def test_etb_and_htb_never_block():
    for s in (BORROW_ETB, BORROW_HTB):
        assert is_blocking(s) is False
        assert is_blocking(s, allow_unknown=True) is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
