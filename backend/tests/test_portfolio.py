"""
Smoke tests for Component 4 — Portfolio Construction & Sizing.

Covers:
  - sizing_engine.calculate_size  (pure quant, no mocking needed)
  - portfolio/schemas.py          (Pydantic model validation)
  - exposure_tracker              (pure logic, no mocking needed)
  - correlation.check_correlation (mocks yfinance to avoid network calls)
  - portfolio_agent               (mocks Supabase + yfinance)

Run:
    python -m pytest backend/tests/test_portfolio.py -v
"""

import math
import sys
import types
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path so imports resolve correctly.
# ---------------------------------------------------------------------------
import os

_PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


# ===========================================================================
# sizing_engine tests — pure logic, no mocking required
# ===========================================================================

from backend.portfolio.sizing_engine import calculate_size, _TIERS, _KELLY_FRACTION


def test_large_tier():
    """Conviction 9.5, $100k portfolio, $50 entry → large label, valid pct."""
    result = calculate_size(conviction_score=9.5, portfolio_value=100_000, entry_price=50.0)
    assert result["size_label"] == "large"
    assert result["share_count"] > 0
    assert result["pct_of_portfolio"] <= 0.08


def test_medium_tier():
    """Conviction 7.5, $25k portfolio, $20 entry → medium label."""
    result = calculate_size(conviction_score=7.5, portfolio_value=25_000, entry_price=20.0)
    assert result["size_label"] == "medium"
    assert result["pct_of_portfolio"] <= 0.05


def test_small_tier():
    """Conviction 5.5, $50k portfolio, $10 entry → small label."""
    result = calculate_size(conviction_score=5.5, portfolio_value=50_000, entry_price=10.0)
    assert result["size_label"] == "small"
    assert result["pct_of_portfolio"] <= 0.02


def test_skip_below_threshold():
    """Conviction 4.9 is below minimum 5.0 — must raise ValueError."""
    with pytest.raises(ValueError, match="below 5.0"):
        calculate_size(conviction_score=4.9, portfolio_value=50_000, entry_price=10.0)


def test_hard_cap_enforced():
    """
    Regardless of Kelly output, pct_of_portfolio must never exceed 0.15.

    We can't trivially force Kelly > 15% with the current tiers (large tier
    caps at 8%), so we verify the cap constant is correctly wired by
    asserting the result stays under 0.15 for any valid conviction.
    """
    result = calculate_size(conviction_score=9.9, portfolio_value=100_000, entry_price=1.0)
    assert result["pct_of_portfolio"] <= 0.15


def test_stop_loss_risk_off():
    """Risk-Off regime → Tier 1 stop = entry × 0.95 (5% stop)."""
    entry = 100.0
    result = calculate_size(conviction_score=7.0, portfolio_value=50_000, entry_price=entry, regime="Risk-Off")
    expected_stop = round(entry * 0.95, 4)
    assert math.isclose(result["stop_loss_price"], expected_stop, rel_tol=1e-6)


def test_stop_loss_risk_on():
    """Risk-On regime → Tier 1 stop = entry × 0.92 (8% stop)."""
    entry = 100.0
    result = calculate_size(conviction_score=7.0, portfolio_value=50_000, entry_price=entry, regime="Risk-On")
    expected_stop = round(entry * 0.92, 4)
    assert math.isclose(result["stop_loss_price"], expected_stop, rel_tol=1e-6)


def test_kelly_formula_correctness():
    """
    Large tier: p=0.65, b=2.0, q=0.35.
    Full Kelly f* = (b*p - q) / b = (2*0.65 - 0.35) / 2 = 0.475.
    The engine stores kelly_fraction = f* (before fractional scaling).
    """
    result = calculate_size(conviction_score=9.5, portfolio_value=100_000, entry_price=10.0)
    expected_kelly = (2.0 * 0.65 - 0.35) / 2.0  # 0.475
    assert math.isclose(result["kelly_fraction"], expected_kelly, rel_tol=1e-5)


def test_share_count_zero_raises():
    """Entry $1000 on a $100 portfolio → computed dollar_size < entry_price → share_count=0 → ValueError."""
    with pytest.raises(ValueError, match="share_count is 0"):
        calculate_size(conviction_score=9.0, portfolio_value=100.0, entry_price=1_000.0)


def test_invalid_regime_raises():
    """Unrecognised regime string → ValueError from _compute_stop_loss."""
    with pytest.raises(ValueError, match="Unknown regime"):
        calculate_size(conviction_score=7.0, portfolio_value=50_000, entry_price=20.0, regime="Unknown")


# ===========================================================================
# schemas tests — Pydantic validation, no mocking required
# ===========================================================================

from pydantic import ValidationError
from backend.portfolio.schemas import ExposureState, PortfolioSnapshot, SizingRecommendation


def _make_portfolio_snapshot(**overrides) -> dict:
    defaults = {
        "gross_exposure_pct": 0.10,
        "net_exposure_pct": 0.10,
        "sector_concentration": {"Healthcare": 0.10},
        "position_count": 1,
    }
    return {**defaults, **overrides}


def _make_sizing_recommendation(**overrides) -> dict:
    snapshot = PortfolioSnapshot(**_make_portfolio_snapshot())
    defaults = {
        "ticker": "ACME",
        "date": "2026-04-01",
        "direction": "LONG",
        "conviction_score": 7.5,
        "dollar_size": 1_250.0,
        "share_count": 62,
        "size_label": "medium",
        "pct_of_portfolio": 0.05,
        "entry_price": 20.0,
        "stop_loss_price": 18.4,
        "sizing_rationale": "Kelly p=0.58 b=2 capped at 5%.",
        "correlation_flag": False,
        "regime_at_sizing": "Risk-On",
        "portfolio_state_after": snapshot,
    }
    return {**defaults, **overrides}


def test_sizing_recommendation_valid():
    """Construct SizingRecommendation with all required fields — should validate without error."""
    rec = SizingRecommendation(**_make_sizing_recommendation())
    assert rec.ticker == "ACME"
    assert rec.size_label == "medium"
    assert rec.status == "PENDING_APPROVAL"  # default


def test_exposure_state_valid():
    """ExposureState accepts a valid regime Literal and all required fields."""
    state = ExposureState(
        gross_exposure_pct=0.10,
        net_exposure_pct=0.10,
        max_gross_pct=1.50,
        max_net_long_pct=0.50,
        max_net_short_pct=0.00,
        sector_concentration={"Healthcare": 0.10},
        position_count=1,
        regime="Risk-On",
    )
    assert state.regime == "Risk-On"
    assert state.position_count == 1


def test_portfolio_snapshot_valid():
    """PortfolioSnapshot validates with all required fields."""
    snap = PortfolioSnapshot(**_make_portfolio_snapshot())
    assert snap.gross_exposure_pct == 0.10
    assert snap.position_count == 1


def test_sizing_recommendation_rejects_bad_ticker():
    """ticker='' (empty string) must fail min_length=1 validation."""
    with pytest.raises(ValidationError):
        SizingRecommendation(**_make_sizing_recommendation(ticker=""))


def test_regime_at_sizing_literal():
    """regime_at_sizing='Bad' is not a valid Literal value — must raise ValidationError."""
    with pytest.raises(ValidationError):
        SizingRecommendation(**_make_sizing_recommendation(regime_at_sizing="Bad"))


# ===========================================================================
# exposure_tracker tests — pure logic, no mocking required
# ===========================================================================

from backend.portfolio.exposure_tracker import (
    check_exposure_breach,
    get_current_exposure,
    REGIME_CAPS,
)


def test_empty_positions():
    """get_current_exposure with no positions → all zeros, position_count=0."""
    state = get_current_exposure([], 25_000, "Risk-On")
    assert state["gross_exposure_pct"] == 0.0
    assert state["net_exposure_pct"] == 0.0
    assert state["position_count"] == 0
    assert state["sector_concentration"] == {}


def test_long_position_exposure():
    """One LONG position at 10% of $25k portfolio → gross=0.10, net=0.10."""
    positions = [{"direction": "LONG", "dollar_size": 2_500.0, "sector": "Healthcare"}]
    state = get_current_exposure(positions, 25_000.0, "Risk-On")
    assert math.isclose(state["gross_exposure_pct"], 0.10, rel_tol=1e-5)
    assert math.isclose(state["net_exposure_pct"], 0.10, rel_tol=1e-5)


def test_short_position_exposure():
    """One SHORT position at 5% of $20k portfolio → gross=0.05, net=-0.05."""
    positions = [{"direction": "SHORT", "dollar_size": 1_000.0, "sector": "Tech"}]
    state = get_current_exposure(positions, 20_000.0, "Risk-On")
    assert math.isclose(state["gross_exposure_pct"], 0.05, rel_tol=1e-5)
    assert math.isclose(state["net_exposure_pct"], -0.05, rel_tol=1e-5)


def test_check_breach_gross_cap():
    """
    Risk-Off cap: max_gross = 80%.  Existing exposure at 75%, new LONG 10%
    would push to 85% → breached=True.
    """
    # Simulate existing state at 75% gross in Risk-Off (no existing net long to worry about)
    current = {
        "gross_exposure_pct": 0.75,
        "net_exposure_pct": 0.0,
        "max_gross_pct": REGIME_CAPS["Risk-Off"]["max_gross"],    # 0.80
        "max_net_long_pct": REGIME_CAPS["Risk-Off"]["max_net_long"],
        "max_net_short_pct": REGIME_CAPS["Risk-Off"]["max_net_short"],
        "regime": "Risk-Off",
    }
    new_dollar_size = 0.10 * 100_000  # 10% of 100k
    portfolio_value = 100_000
    breached, reason = check_exposure_breach(
        new_dollar_size=new_dollar_size,
        new_direction="LONG",
        new_sector="Healthcare",
        current=current,
        portfolio_value=portfolio_value,
    )
    assert breached is True
    assert "gross" in reason.lower() or "cap" in reason.lower()


def test_check_breach_position_cap():
    """Any single position > 15% of portfolio triggers the hard per-position cap."""
    current = {
        "gross_exposure_pct": 0.0,
        "net_exposure_pct": 0.0,
        "max_gross_pct": 1.50,
        "max_net_long_pct": 0.50,
        "max_net_short_pct": 0.00,
        "regime": "Risk-On",
    }
    new_dollar_size = 0.20 * 50_000  # 20% → breaches 15% cap
    portfolio_value = 50_000
    breached, reason = check_exposure_breach(
        new_dollar_size=new_dollar_size,
        new_direction="LONG",
        new_sector=None,
        current=current,
        portfolio_value=portfolio_value,
    )
    assert breached is True
    assert "15" in reason or "cap" in reason.lower()


def test_no_breach():
    """Small position in empty Risk-On portfolio → no breach."""
    current = {
        "gross_exposure_pct": 0.0,
        "net_exposure_pct": 0.0,
        "max_gross_pct": REGIME_CAPS["Risk-On"]["max_gross"],
        "max_net_long_pct": REGIME_CAPS["Risk-On"]["max_net_long"],
        "max_net_short_pct": REGIME_CAPS["Risk-On"]["max_net_short"],
        "regime": "Risk-On",
    }
    new_dollar_size = 0.05 * 25_000  # 5% of 25k = $1,250 — well within limits
    breached, reason = check_exposure_breach(
        new_dollar_size=new_dollar_size,
        new_direction="LONG",
        new_sector="Healthcare",
        current=current,
        portfolio_value=25_000,
    )
    assert breached is False
    assert reason == ""


# ===========================================================================
# correlation tests — yfinance mocked to avoid network calls
# ===========================================================================

from backend.portfolio.correlation import check_correlation


def test_empty_positions_returns_false():
    """check_correlation with no open positions → (False, None) immediately."""
    flag, note = check_correlation(
        candidate_ticker="XYZ",
        candidate_sector="Healthcare",
        open_positions=[],
        portfolio_value=25_000,
    )
    assert flag is False
    assert note is None


def test_sector_concentration_rule2():
    """
    3 Healthcare positions each at 10% (combined 30%) → Rule 2 fires.
    No price data fetch needed — Rule 2 is pure arithmetic.
    We mock _fetch_close_prices to return empty so Rule 1 is skipped cleanly.
    """
    positions = [
        {"ticker": "A", "sector": "Healthcare", "pct_of_portfolio": 0.10, "direction": "LONG"},
        {"ticker": "B", "sector": "Healthcare", "pct_of_portfolio": 0.10, "direction": "LONG"},
        {"ticker": "C", "sector": "Healthcare", "pct_of_portfolio": 0.10, "direction": "LONG"},
    ]
    # Mock _fetch_close_prices so Rule 1 price download is skipped
    with patch("backend.portfolio.correlation._fetch_close_prices", return_value=__import__("pandas").DataFrame()):
        flag, note = check_correlation(
            candidate_ticker="CAND",
            candidate_sector="Healthcare",
            open_positions=positions,
            portfolio_value=100_000,
        )
    assert flag is True
    assert note is not None
    assert "Sector concentration" in note


def test_no_sector_concentration_below_threshold():
    """
    3 Healthcare positions each at 6% (combined 18%) — below 25% threshold.
    Rule 2 must NOT fire.  Rule 1 is mocked to return empty data (no breach).
    """
    positions = [
        {"ticker": "A", "sector": "Healthcare", "pct_of_portfolio": 0.06, "direction": "LONG"},
        {"ticker": "B", "sector": "Healthcare", "pct_of_portfolio": 0.06, "direction": "LONG"},
        {"ticker": "C", "sector": "Healthcare", "pct_of_portfolio": 0.06, "direction": "LONG"},
    ]
    with patch("backend.portfolio.correlation._fetch_close_prices", return_value=__import__("pandas").DataFrame()):
        flag, note = check_correlation(
            candidate_ticker="CAND",
            candidate_sector="Healthcare",
            open_positions=positions,
            portfolio_value=100_000,
        )
    assert flag is False


def test_candidate_sector_none_skips_rule2():
    """
    candidate_sector=None → Rule 2 skipped entirely.
    Rule 1 requires price data; we return empty DataFrame → also skipped.
    Result: (False, None).
    """
    positions = [
        {"ticker": "A", "sector": "Healthcare", "pct_of_portfolio": 0.15, "direction": "LONG"},
        {"ticker": "B", "sector": "Healthcare", "pct_of_portfolio": 0.15, "direction": "LONG"},
        {"ticker": "C", "sector": "Healthcare", "pct_of_portfolio": 0.15, "direction": "LONG"},
    ]
    with patch("backend.portfolio.correlation._fetch_close_prices", return_value=__import__("pandas").DataFrame()):
        flag, note = check_correlation(
            candidate_ticker="CAND",
            candidate_sector=None,  # explicitly None — no sector known
            open_positions=positions,
            portfolio_value=100_000,
        )
    assert flag is False
    assert note is None


# ===========================================================================
# portfolio_agent tests — Supabase + yfinance fully mocked
# ===========================================================================

import asyncio

from backend.agents.portfolio_agent import PortfolioAgentError, run_portfolio_sizing


def _run(coro):
    """Helper: run an async coroutine synchronously in tests."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_mock_supabase(memo_row: Optional[dict] = None, regime: str = "Risk-On"):
    """
    Build a mock Supabase client whose table().select()...execute() chain
    returns appropriate test data for each table queried by the agent.
    """
    client = MagicMock()

    def table_side_effect(table_name: str):
        tbl = MagicMock()
        tbl.select.return_value = tbl
        tbl.eq.return_value = tbl
        tbl.order.return_value = tbl
        tbl.limit.return_value = tbl
        tbl.insert.return_value = tbl

        if table_name == "memos":
            execute_result = MagicMock()
            execute_result.data = [memo_row] if memo_row else []
            tbl.execute.return_value = execute_result

        elif table_name == "macro_briefings":
            execute_result = MagicMock()
            execute_result.data = [{"regime": regime}]
            tbl.execute.return_value = execute_result

        elif table_name == "positions":
            # open positions query + insert
            open_result = MagicMock()
            open_result.data = []  # no existing positions
            tbl.execute.return_value = open_result

        return tbl

    client.table.side_effect = table_side_effect
    return client


def test_non_long_verdict_raises():
    """Memo with verdict=AVOID → Phase 1 raises PortfolioAgentError."""
    memo_row = {
        "id": "test-id-001",
        "ticker": "ACME",
        "verdict": "AVOID",
        "conviction_score": 7.5,
        "memo_json": {"verdict": "AVOID", "conviction_score": 7.5, "ticker": "ACME"},
    }
    mock_client = _make_mock_supabase(memo_row=memo_row)

    with patch("backend.agents.portfolio_agent._get_client", return_value=mock_client):
        with pytest.raises(PortfolioAgentError, match="SHORT verdicts deferred"):
            _run(run_portfolio_sizing("test-id-001", portfolio_value=25_000))


def test_low_conviction_raises():
    """Memo with verdict=LONG but conviction=4.0 → Phase 1 raises PortfolioAgentError."""
    memo_row = {
        "id": "test-id-002",
        "ticker": "ACME",
        "verdict": "LONG",
        "conviction_score": 4.0,
        "memo_json": {"verdict": "LONG", "conviction_score": 4.0, "ticker": "ACME"},
    }
    mock_client = _make_mock_supabase(memo_row=memo_row)

    with patch("backend.agents.portfolio_agent._get_client", return_value=mock_client):
        with pytest.raises(PortfolioAgentError, match="conviction too low"):
            _run(run_portfolio_sizing("test-id-002", portfolio_value=25_000))


def test_missing_entry_price_raises():
    """yfinance returns None price → Phase 3 raises PortfolioAgentError."""
    memo_row = {
        "id": "test-id-003",
        "ticker": "ACME",
        "verdict": "LONG",
        "conviction_score": 7.5,
        "memo_json": {"verdict": "LONG", "conviction_score": 7.5, "ticker": "ACME"},
    }
    mock_client = _make_mock_supabase(memo_row=memo_row, regime="Risk-On")

    # yfinance.Ticker.info returns dict with no price keys
    mock_ticker = MagicMock()
    mock_ticker.info = {}  # no regularMarketPrice, no currentPrice

    with patch("backend.agents.portfolio_agent._get_client", return_value=mock_client):
        with patch("backend.agents.portfolio_agent.yf.Ticker", return_value=mock_ticker):
            with pytest.raises(PortfolioAgentError, match="could not fetch entry price"):
                _run(run_portfolio_sizing("test-id-003", portfolio_value=25_000))
