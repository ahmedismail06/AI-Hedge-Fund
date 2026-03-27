"""
Unit tests for backend/screener/scorer._normalize_universe

The function percentile-ranks a dict of {ticker: float|None} values to [0, 10].

Code paths covered:
  - len(valid) < 2 guard (all None, one valid value, empty dict)
  - len(set(sorted_vals)) == 1 guard (all identical)
  - higher_is_better=True  (lowest raw → 0.0, highest → 10.0)
  - higher_is_better=False (lowest raw → 10.0, highest → 0.0)
  - Tied raw values → average rank (symmetric)
  - None tickers get neutral 5.0 amid ranked peers
  - Output always in [0.0, 10.0]
"""

import random
import pytest

from backend.screener.scorer import _normalize_universe


# ---------------------------------------------------------------------------
# Guard path: len(valid) < 2 — all tickers get neutral 5.0
# ---------------------------------------------------------------------------

def test_empty_dict_returns_empty_dict():
    assert _normalize_universe({}) == {}


def test_all_none_returns_neutral_for_all():
    result = _normalize_universe({"A": None, "B": None, "C": None})
    assert result == {"A": 5.0, "B": 5.0, "C": 5.0}


def test_single_valid_value_returns_neutral_for_all():
    """Only one non-None value → len(valid)=1 < 2 → all 5.0."""
    result = _normalize_universe({"A": 42.0, "B": None})
    assert result == {"A": 5.0, "B": 5.0}


# ---------------------------------------------------------------------------
# Guard path: all identical → all tickers get neutral 5.0
# ---------------------------------------------------------------------------

def test_all_identical_values_returns_neutral():
    result = _normalize_universe({"X": 7.0, "Y": 7.0, "Z": 7.0})
    assert result == {"X": 5.0, "Y": 5.0, "Z": 5.0}


# ---------------------------------------------------------------------------
# Core ranking — higher_is_better=True (default)
# ---------------------------------------------------------------------------

def test_two_values_extremes_higher_is_better():
    """Lowest raw → 0.0, highest → 10.0."""
    result = _normalize_universe({"LOW": 0.0, "HIGH": 10.0})
    assert result["HIGH"] == 10.0
    assert result["LOW"] == 0.0


def test_three_values_correct_order_higher_is_better():
    """Middle value gets a score strictly between the extremes."""
    result = _normalize_universe({"A": 10.0, "B": 5.0, "C": 0.0})
    assert result["A"] > result["B"] > result["C"]
    assert result["A"] == 10.0
    assert result["C"] == 0.0


# ---------------------------------------------------------------------------
# Core ranking — higher_is_better=False
# ---------------------------------------------------------------------------

def test_two_values_extremes_lower_is_better():
    """Lowest raw → 10.0 (best score); highest raw → 0.0 (worst)."""
    result = _normalize_universe({"CHEAP": 1.0, "PRICEY": 10.0}, higher_is_better=False)
    assert result["CHEAP"] == 10.0
    assert result["PRICEY"] == 0.0


# ---------------------------------------------------------------------------
# Tied values use average rank
# ---------------------------------------------------------------------------

def test_tied_values_receive_same_score():
    result = _normalize_universe({"A": 5.0, "B": 5.0, "C": 10.0})
    assert result["A"] == result["B"]


def test_tied_values_average_rank_is_correct():
    """
    sorted=[5,5,10], n=3.
    5.0: positions=[0,1] → avg_rank=0.5 → pct=0.5/2=0.25 → score=2.5
    10.0: positions=[2]  → avg_rank=2.0 → pct=1.0         → score=10.0
    """
    result = _normalize_universe({"A": 5.0, "B": 5.0, "C": 10.0})
    assert result["A"] == pytest.approx(2.5, abs=0.01)
    assert result["B"] == pytest.approx(2.5, abs=0.01)
    assert result["C"] == pytest.approx(10.0, abs=0.01)


# ---------------------------------------------------------------------------
# None values get neutral amid ranked peers
# ---------------------------------------------------------------------------

def test_none_values_get_neutral_amid_ranked_peers():
    result = _normalize_universe({"HIGH": 100.0, "LOW": 0.0, "MISSING": None})
    assert result["MISSING"] == 5.0
    assert result["HIGH"] == 10.0
    assert result["LOW"] == 0.0


# ---------------------------------------------------------------------------
# Output range always [0.0, 10.0]
# ---------------------------------------------------------------------------

def test_output_always_in_0_to_10_range():
    random.seed(42)
    values = {f"T{i}": random.uniform(-1000.0, 1000.0) for i in range(30)}
    result = _normalize_universe(values)
    for ticker, score in result.items():
        assert 0.0 <= score <= 10.0, f"{ticker}: score {score} out of range"
