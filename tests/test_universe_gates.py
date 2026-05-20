import pytest
from backend.screener.universe import filter_by_profitability, UniverseCandidate

def test_pre_revenue_biotech_exclusion():
    """
    Universe gate 3 (PRE_REVENUE_BIOTECH):
    Companies with gm > 0.95 should be excluded.
    """
    universe = [
        UniverseCandidate(ticker="BIO1", market_cap_m=100, sector="Healthcare"),
        UniverseCandidate(ticker="BIO2", market_cap_m=100, sector="Healthcare"),
    ]
    
    # BIO1 has gm = 0.96 (Excluded)
    # BIO2 has gm = 0.80 (Kept)
    raw_data_map = {
        "BIO1": {
            "fmp": {
                "income_statement": [{"revenue": 1000, "grossProfit": 960}]
            }
        },
        "BIO2": {
            "fmp": {
                "income_statement": [{"revenue": 1000, "grossProfit": 800}]
            }
        }
    }
    
    filtered = filter_by_profitability(universe, raw_data_map)
    
    tickers = [c.ticker for c in filtered]
    assert "BIO2" in tickers
    assert "BIO1" not in tickers

def test_broken_unit_economics_exclusion():
    """
    Universe gate 1 (BROKEN_UNIT_ECONOMICS):
    Companies with gm < sector_floor should be excluded.
    """
    universe = [
        UniverseCandidate(ticker="SAAS_LOW", market_cap_m=100, sector="SaaS"),
        UniverseCandidate(ticker="SAAS_OK", market_cap_m=100, sector="SaaS"),
    ]
    
    # SaaS floor is 0.15
    raw_data_map = {
        "SAAS_LOW": {
            "fmp": {
                "income_statement": [{"revenue": 1000, "grossProfit": 100}] # gm = 0.1
            }
        },
        "SAAS_OK": {
            "fmp": {
                "income_statement": [{"revenue": 1000, "grossProfit": 200}] # gm = 0.2
            }
        }
    }
    
    filtered = filter_by_profitability(universe, raw_data_map)
    
    tickers = [c.ticker for c in filtered]
    assert "SAAS_OK" in tickers
    assert "SAAS_LOW" not in tickers

def test_insufficient_quality_data_exclusion():
    """
    Universe gate 4 (INSUFFICIENT_QUALITY_DATA):
    gm is None AND rev_growth is None → excluded
    """
    universe = [
        UniverseCandidate(ticker="NO_DATA", market_cap_m=100, sector="Other"),
        UniverseCandidate(ticker="HAS_DATA", market_cap_m=100, sector="Other"),
    ]
    
    raw_data_map = {
        "NO_DATA": {
            "fmp": {
                "income_statement": []
            }
        },
        "HAS_DATA": {
            "fmp": {
                "income_statement": [{"revenue": 1000, "grossProfit": 500}]
            }
        }
    }
    
    filtered = filter_by_profitability(universe, raw_data_map)
    
    tickers = [c.ticker for c in filtered]
    assert "HAS_DATA" in tickers
    assert "NO_DATA" not in tickers
