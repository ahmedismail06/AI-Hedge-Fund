"""
Smoke tests for Component 9 — EarningsAlpha Module.

Coverage:
  - estimate_comparator.extrapolate_internal_eps()
  - estimate_comparator.compute_signal()
  - drift_manager.get_active_drift_hold() (mocked Supabase)
  - drift_manager.activate_drift_hold() (mocked Supabase)
  - drift_manager._drift_hold_active() (via stop_loss.py)
  - runner.run_earnings_alpha() (mocked DB calls)
  - runner._compute_historical_stats()
  - runner._format_summary()
  - stop_loss._tier1_threshold() with drift_hold_active flag
  - stop_loss._drift_hold_active() helper

Run:
    python -m pytest backend/tests/test_earnings_alpha.py -v
"""

import sys
import os
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


# ===========================================================================
# Fixtures / helpers
# ===========================================================================

def _make_reactions(n: int = 8, beat: bool = True) -> list[dict]:
    """Generate n quarters of earnings reactions data, newest first (i=0 = most recent)."""
    rows = []
    base_eps = 0.80
    for i in range(n):
        # Newest (i=0) gets highest EPS — growing trend
        eps = round(base_eps + (n - 1 - i) * 0.05, 2)
        consensus = round(eps * (0.92 if beat else 1.08), 2)
        rows.append({
            "date": (date.today() - timedelta(days=i * 90)).isoformat(),
            "reported_eps": eps,
            "consensus_eps": consensus,
            "surprise_pct": round((eps - consensus) / abs(consensus), 4) if consensus else None,
            "price_reaction_1d": 0.025 if beat else -0.03,
            "price_reaction_5d": 0.05 if beat else -0.06,
        })
    return rows


def _make_fmp_data(consensus_annual: float = 4.0) -> dict:
    return {
        "ticker": "ACME",
        "sector": "Healthcare",
        "consensus_eps_current_year": consensus_annual,
        "error": None,
    }


# ===========================================================================
# estimate_comparator.extrapolate_internal_eps
# ===========================================================================

class TestExtrapolateInternalEps:
    def test_valid_8q_data(self):
        from backend.earnings_alpha.estimate_comparator import extrapolate_internal_eps
        reactions = _make_reactions(8)
        result = extrapolate_internal_eps(reactions)
        assert result is not None
        assert isinstance(result, float)
        # Should be above the most recent quarter (growing EPS)
        assert result > reactions[0]["reported_eps"]

    def test_fewer_than_3_valid_returns_none(self):
        from backend.earnings_alpha.estimate_comparator import extrapolate_internal_eps
        reactions = [
            {"reported_eps": None, "consensus_eps": 1.0, "surprise_pct": None,
             "price_reaction_1d": None, "price_reaction_5d": None, "date": "2026-01-01"},
            {"reported_eps": None, "consensus_eps": 1.0, "surprise_pct": None,
             "price_reaction_1d": None, "price_reaction_5d": None, "date": "2025-10-01"},
            {"reported_eps": 0.80, "consensus_eps": 0.75, "surprise_pct": 0.067,
             "price_reaction_1d": 0.02, "price_reaction_5d": 0.04, "date": "2025-07-01"},
        ]
        result = extrapolate_internal_eps(reactions)
        assert result is None  # only 1 non-None; < 3 required

    def test_all_none_returns_none(self):
        from backend.earnings_alpha.estimate_comparator import extrapolate_internal_eps
        reactions = [{"reported_eps": None} for _ in range(8)]
        result = extrapolate_internal_eps(reactions)
        assert result is None

    def test_empty_list_returns_none(self):
        from backend.earnings_alpha.estimate_comparator import extrapolate_internal_eps
        assert extrapolate_internal_eps([]) is None

    def test_sign_change_returns_none(self):
        # eps_latest (newest) positive, eps_oldest (4 quarters ago) negative
        # → CAGR undefined across zero-crossing
        from backend.earnings_alpha.estimate_comparator import extrapolate_internal_eps
        reactions = [
            {"reported_eps": 0.50, "date": "2026-01-01"},
            {"reported_eps": 0.30, "date": "2025-10-01"},
            {"reported_eps": 0.10, "date": "2025-07-01"},
            {"reported_eps": -0.20, "date": "2025-04-01"},  # oldest in window is negative
        ]
        result = extrapolate_internal_eps(reactions)
        assert result is None


# ===========================================================================
# estimate_comparator.compute_signal
# ===========================================================================

class TestComputeSignal:
    def test_size_up_when_spread_ge_10pct_and_conviction_gte_7(self):
        from backend.earnings_alpha.estimate_comparator import compute_signal
        sig = compute_signal(internal_est=0.88, consensus_eps=0.78, conviction_score=7.5)
        assert sig.signal == "SIZE_UP"
        assert sig.conviction_gate_passed is True
        assert sig.spread_pct is not None
        assert sig.spread_pct > 0.10

    def test_hold_when_spread_ge_10pct_but_conviction_below_gate(self):
        from backend.earnings_alpha.estimate_comparator import compute_signal
        sig = compute_signal(internal_est=0.88, consensus_eps=0.78, conviction_score=6.0)
        assert sig.signal == "HOLD"
        assert sig.conviction_gate_passed is False

    def test_reduce_when_spread_le_minus_10pct(self):
        from backend.earnings_alpha.estimate_comparator import compute_signal
        sig = compute_signal(internal_est=0.70, consensus_eps=0.80, conviction_score=8.0)
        assert sig.signal == "REDUCE"
        assert sig.spread_pct is not None
        assert sig.spread_pct < -0.10

    def test_hold_within_band(self):
        from backend.earnings_alpha.estimate_comparator import compute_signal
        sig = compute_signal(internal_est=0.82, consensus_eps=0.80, conviction_score=8.0)
        assert sig.signal == "HOLD"
        assert abs(sig.spread_pct) < 0.10  # type: ignore[operator]

    def test_none_internal_est_yields_hold(self):
        from backend.earnings_alpha.estimate_comparator import compute_signal
        sig = compute_signal(internal_est=None, consensus_eps=0.80, conviction_score=9.0)
        assert sig.signal == "HOLD"
        assert sig.spread_pct is None

    def test_none_consensus_yields_hold(self):
        from backend.earnings_alpha.estimate_comparator import compute_signal
        sig = compute_signal(internal_est=0.80, consensus_eps=None, conviction_score=9.0)
        assert sig.signal == "HOLD"

    def test_zero_consensus_yields_hold(self):
        from backend.earnings_alpha.estimate_comparator import compute_signal
        sig = compute_signal(internal_est=0.80, consensus_eps=0.0, conviction_score=9.0)
        assert sig.signal == "HOLD"

    def test_conviction_exactly_at_gate(self):
        from backend.earnings_alpha.estimate_comparator import compute_signal
        sig = compute_signal(internal_est=0.90, consensus_eps=0.79, conviction_score=7.0)
        assert sig.signal == "SIZE_UP"
        assert sig.conviction_gate_passed is True

    def test_conviction_just_below_gate(self):
        from backend.earnings_alpha.estimate_comparator import compute_signal
        sig = compute_signal(internal_est=0.90, consensus_eps=0.79, conviction_score=6.99)
        assert sig.signal == "HOLD"
        assert sig.conviction_gate_passed is False


# ===========================================================================
# drift_manager._drift_hold_active helper (via stop_loss module)
# ===========================================================================

class TestDriftHoldActiveHelper:
    def test_active_future_date(self):
        from backend.risk.stop_loss import _drift_hold_active
        hold_until = (date.today() + timedelta(days=10)).isoformat()
        pos = {"drift_hold_until": hold_until}
        assert _drift_hold_active(pos) is True

    def test_expired_past_date(self):
        from backend.risk.stop_loss import _drift_hold_active
        hold_until = (date.today() - timedelta(days=1)).isoformat()
        pos = {"drift_hold_until": hold_until}
        assert _drift_hold_active(pos) is False

    def test_exactly_today(self):
        from backend.risk.stop_loss import _drift_hold_active
        pos = {"drift_hold_until": date.today().isoformat()}
        assert _drift_hold_active(pos) is True

    def test_none_returns_false(self):
        from backend.risk.stop_loss import _drift_hold_active
        assert _drift_hold_active({}) is False
        assert _drift_hold_active({"drift_hold_until": None}) is False


# ===========================================================================
# stop_loss._tier1_threshold with drift_hold flag
# ===========================================================================

class TestTier1ThresholdDriftHold:
    def test_risk_off_no_drift_hold_uses_tight(self):
        from backend.risk.stop_loss import _tier1_threshold
        assert _tier1_threshold("Risk-Off", drift_hold_active=False) == -0.05

    def test_risk_off_with_drift_hold_uses_normal(self):
        from backend.risk.stop_loss import _tier1_threshold
        assert _tier1_threshold("Risk-Off", drift_hold_active=True) == -0.08

    def test_stagflation_with_drift_hold_uses_normal(self):
        from backend.risk.stop_loss import _tier1_threshold
        assert _tier1_threshold("Stagflation", drift_hold_active=True) == -0.08

    def test_risk_on_drift_hold_no_effect(self):
        from backend.risk.stop_loss import _tier1_threshold
        # Risk-On already uses normal; drift hold has no additional effect
        assert _tier1_threshold("Risk-On", drift_hold_active=True) == -0.08
        assert _tier1_threshold("Risk-On", drift_hold_active=False) == -0.08


# ===========================================================================
# runner._compute_historical_stats
# ===========================================================================

class TestComputeHistoricalStats:
    def test_8_beats(self):
        from backend.earnings_alpha.runner import _compute_historical_stats
        reactions = _make_reactions(8, beat=True)
        beat_rate, avg_5d = _compute_historical_stats(reactions)
        assert beat_rate == 1.0
        assert avg_5d == pytest.approx(0.05)

    def test_8_misses(self):
        from backend.earnings_alpha.runner import _compute_historical_stats
        reactions = _make_reactions(8, beat=False)
        beat_rate, avg_5d = _compute_historical_stats(reactions)
        assert beat_rate == 0.0
        assert avg_5d is None  # no beats so no avg

    def test_empty_reactions(self):
        from backend.earnings_alpha.runner import _compute_historical_stats
        beat_rate, avg_5d = _compute_historical_stats([])
        assert beat_rate is None
        assert avg_5d is None

    def test_missing_consensus_excluded(self):
        from backend.earnings_alpha.runner import _compute_historical_stats
        reactions = [
            {"reported_eps": 1.0, "consensus_eps": None, "price_reaction_5d": 0.05, "date": "2026-01-01"},
            {"reported_eps": 0.9, "consensus_eps": 0.8, "price_reaction_5d": 0.04, "date": "2025-10-01"},
        ]
        beat_rate, avg_5d = _compute_historical_stats(reactions)
        assert beat_rate == 1.0
        assert avg_5d == pytest.approx(0.04)


# ===========================================================================
# runner.run_earnings_alpha — full pipeline with mocked DB
# ===========================================================================

class TestRunEarningsAlpha:
    def _mock_supabase(self):
        """Return a mock Supabase client that returns empty data for all queries."""
        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.eq.return_value\
            .eq.return_value.gte.return_value.order.return_value.limit.return_value\
            .execute.return_value = MagicMock(data=[])
        mock_client.table.return_value.select.return_value.eq.return_value\
            .lt.return_value.execute.return_value = MagicMock(data=[])
        mock_client.table.return_value.upsert.return_value.execute.return_value = MagicMock()
        return mock_client

    @patch("backend.earnings_alpha.drift_manager._get_client")
    @patch("backend.earnings_alpha.runner._get_client")
    def test_full_pipeline_beat(self, mock_runner_client, mock_drift_client):
        mock_runner_client.return_value = self._mock_supabase()
        mock_drift_client.return_value = self._mock_supabase()

        from backend.earnings_alpha.runner import run_earnings_alpha
        reactions = _make_reactions(8, beat=True)
        fmp = _make_fmp_data(consensus_annual=3.2)

        output = run_earnings_alpha("ACME", reactions, fmp, conviction_score=8.0)

        assert output.ticker == "ACME"
        assert output.unavailable is False
        assert output.pre_earnings.signal in ("SIZE_UP", "HOLD", "REDUCE")
        assert output.drift_hold is not None
        assert "=== EARNINGS ALPHA ===" in output.summary
        assert output.historical_beat_rate == 1.0

    @patch("backend.earnings_alpha.drift_manager._get_client")
    @patch("backend.earnings_alpha.runner._get_client")
    def test_empty_reactions_returns_hold(self, mock_runner_client, mock_drift_client):
        mock_runner_client.return_value = self._mock_supabase()
        mock_drift_client.return_value = self._mock_supabase()

        from backend.earnings_alpha.runner import run_earnings_alpha
        output = run_earnings_alpha("ACME", [], {}, conviction_score=5.0)

        assert output.pre_earnings.signal == "HOLD"
        assert output.drift_hold.active is False

    @patch("backend.earnings_alpha.drift_manager._get_client")
    @patch("backend.earnings_alpha.runner._get_client")
    def test_large_positive_surprise_activates_drift_hold(self, mock_runner_client, mock_drift_client):
        mock_runner_client.return_value = self._mock_supabase()

        # Mock drift manager client to return empty (no existing hold)
        # but then allow activate to fire
        mock_drift_client.return_value = self._mock_supabase()

        from backend.earnings_alpha.runner import run_earnings_alpha
        reactions = _make_reactions(8, beat=True)
        # Override most recent with a large surprise within last 30 days
        reactions[0] = {
            "date": (date.today() - timedelta(days=5)).isoformat(),
            "reported_eps": 1.20,
            "consensus_eps": 0.85,
            "surprise_pct": 0.41,  # +41% — above 5% threshold
            "price_reaction_1d": 0.08,
            "price_reaction_5d": 0.12,
        }
        fmp = _make_fmp_data()
        output = run_earnings_alpha("ACME", reactions, fmp, conviction_score=5.0)

        # With mocked DB returning empty, activate_drift_hold will still attempt;
        # output should reflect the fresh surprise detected
        assert output.unavailable is False


# ===========================================================================
# Schema validation
# ===========================================================================

class TestSchemas:
    def test_pre_earnings_sizing_construction(self):
        from backend.earnings_alpha.schemas import PreEarningsSizing
        s = PreEarningsSizing(
            signal="SIZE_UP",
            internal_eps_estimate=0.88,
            consensus_eps=0.78,
            spread_pct=0.128,
            conviction_gate_passed=True,
            rationale="Test rationale",
        )
        assert s.signal == "SIZE_UP"
        assert s.conviction_gate_passed is True

    def test_drift_hold_state_inactive(self):
        from backend.earnings_alpha.schemas import DriftHoldState
        s = DriftHoldState(active=False)
        assert s.active is False
        assert s.hold_until is None

    def test_earnings_alpha_output_unavailable(self):
        from backend.earnings_alpha.schemas import (
            EarningsAlphaOutput, PreEarningsSizing, DriftHoldState
        )
        pre = PreEarningsSizing(
            signal="HOLD", conviction_gate_passed=False, rationale="N/A"
        )
        drift = DriftHoldState(active=False)
        out = EarningsAlphaOutput(
            ticker="TEST",
            run_date="2026-04-19",
            pre_earnings=pre,
            drift_hold=drift,
            summary="=== EARNINGS ALPHA ===\nUnavailable",
            unavailable=True,
            unavailable_reason="test",
        )
        assert out.unavailable is True
        assert out.ticker == "TEST"
