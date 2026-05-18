"""
Tests for the FMP sector backstop in universe.py.

Polygon's `sic_code` is null or wrong for some tickers (notably mortgage REITs
classified as SIC 6500 instead of 6798). The backstop cross-checks against
FMP's `sector` field on the /profile endpoint and excludes REITs / financials /
energy / utilities / basic materials regardless of what SIC says.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.screener.universe import (
    _FMP_EXCLUDED_SECTORS,
    _FMP_SECTOR_TO_INTERNAL,
    _is_excluded_sic,
    _resolve_sector_with_fmp_backstop,
    _sic_to_sector,
)


# ── _is_excluded_sic null safety ─────────────────────────────────────────────
def test_is_excluded_sic_accepts_none():
    """Previously crashed on None; now returns False so callers MUST use the FMP backstop."""
    assert _is_excluded_sic(None) is False


def test_is_excluded_sic_excludes_reit_range():
    assert _is_excluded_sic(6798) is True  # REITs


def test_is_excluded_sic_excludes_pharma_range():
    assert _is_excluded_sic(2834) is True
    assert _is_excluded_sic(8731) is True  # commercial R&D point


def test_is_excluded_sic_keeps_software():
    assert _is_excluded_sic(7372) is False


# ── _sic_to_sector ───────────────────────────────────────────────────────────
def test_sic_to_sector_excluded_returns_none():
    assert _sic_to_sector(6798) is None  # REIT


def test_sic_to_sector_software_returns_saas():
    assert _sic_to_sector(7372) == "SaaS"


def test_sic_to_sector_real_estate_operator_returns_real_estate():
    # 6500-6552 is "real estate operators" — could be legit OR misclassified REIT.
    # _sic_to_sector returns Real Estate; the FMP backstop catches REIT misclassifications.
    assert _sic_to_sector(6500) == "Real Estate"


# ── _resolve_sector_with_fmp_backstop ────────────────────────────────────────
@pytest.fixture
def mock_fmp_sector():
    """Patch _fetch_fmp_sector to return a controllable value."""
    with patch("backend.screener.universe._fetch_fmp_sector") as m:
        yield m


def test_sic_null_fmp_technology_resolves_to_saas(mock_fmp_sector):
    mock_fmp_sector.return_value = "Technology"
    assert _resolve_sector_with_fmp_backstop("XYZ", None, "fake_key") == "SaaS"


def test_sic_null_fmp_real_estate_excluded(mock_fmp_sector):
    """The AOMR case: Polygon returns null SIC, FMP correctly says Real Estate."""
    mock_fmp_sector.return_value = "Real Estate"
    assert _resolve_sector_with_fmp_backstop("AOMR", None, "fake_key") is None


def test_polygon_real_estate_fmp_real_estate_excluded(mock_fmp_sector):
    """Polygon SIC 6500 maps to Real Estate; FMP confirms REIT → exclude."""
    mock_fmp_sector.return_value = "Real Estate"
    assert _resolve_sector_with_fmp_backstop("REIT_CASE", 6500, "fake_key") is None


def test_polygon_real_estate_fmp_other_sector_keeps_real_estate(mock_fmp_sector):
    """Polygon SIC 6500 + FMP says e.g. Industrials → trust SIC (legit RE operator)."""
    mock_fmp_sector.return_value = "Industrials"
    # FMP didn't say excluded, so we stick with the SIC mapping (Real Estate)
    # since sic_sector was not None.
    assert _resolve_sector_with_fmp_backstop("RE_OP", 6500, "fake_key") == "Real Estate"


def test_polygon_other_fmp_financial_services_excluded(mock_fmp_sector):
    """A ticker SIC-mapped to 'Other' but FMP says Financial Services → exclude."""
    mock_fmp_sector.return_value = "Financial Services"
    assert _resolve_sector_with_fmp_backstop("FINCO", 6020, "fake_key") is None


def test_sic_null_fmp_null_excluded(mock_fmp_sector):
    """No SIC, no FMP — drop the ticker rather than guess."""
    mock_fmp_sector.return_value = None
    assert _resolve_sector_with_fmp_backstop("MYSTERY", None, "fake_key") is None


def test_sic_pharma_no_fmp_call(mock_fmp_sector):
    """Pharma SIC is already excluded — FMP must not be called (wasted API hit)."""
    # _sic_to_sector returns None for excluded SICs; backstop only calls FMP for
    # null OR Real Estate/Other, so excluded SICs short-circuit before FMP.
    mock_fmp_sector.return_value = "Healthcare"
    assert _resolve_sector_with_fmp_backstop("PHARMA", 2834, "fake_key") is None
    mock_fmp_sector.assert_not_called()


def test_sic_saas_no_fmp_call(mock_fmp_sector):
    """Clean SaaS SIC — no FMP call needed."""
    mock_fmp_sector.return_value = "Real Estate"  # would have excluded if called
    assert _resolve_sector_with_fmp_backstop("OKTA", 7372, "fake_key") == "SaaS"
    mock_fmp_sector.assert_not_called()


def test_sector_overrides_win_absolutely():
    """Manual SECTOR_OVERRIDES bypass everything else."""
    with patch.dict("backend.screener.universe.SECTOR_OVERRIDES", {"FORCE": "Consumer"}):
        assert _resolve_sector_with_fmp_backstop("FORCE", 6798, "fake_key") == "Consumer"


# ── FMP sector taxonomy guards ───────────────────────────────────────────────
def test_excluded_sectors_cover_reits_financials_energy_utilities_materials():
    assert "Real Estate" in _FMP_EXCLUDED_SECTORS
    assert "Financial Services" in _FMP_EXCLUDED_SECTORS
    assert "Energy" in _FMP_EXCLUDED_SECTORS
    assert "Utilities" in _FMP_EXCLUDED_SECTORS
    assert "Basic Materials" in _FMP_EXCLUDED_SECTORS


def test_internal_mapping_covers_kept_fmp_sectors():
    for fmp_label in ("Technology", "Healthcare", "Industrials",
                       "Consumer Cyclical", "Consumer Defensive"):
        assert fmp_label in _FMP_SECTOR_TO_INTERNAL


def test_excluded_and_kept_are_disjoint():
    assert _FMP_EXCLUDED_SECTORS.isdisjoint(_FMP_SECTOR_TO_INTERNAL.keys())


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
