"""
Supplementary short-selling universe: $2B–$10B market cap.

Runs weekly (7-day cache TTL). Extends the primary $50M–$2B long universe
upward so the short screener can target larger, more liquid names.

Reuses helper functions from universe.py — no code duplication.
Only fetches FMP key-metrics-ttm per ticker (1 call each) rather than the
full fetch_ticker_data() stack used by the long screener.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

from backend.screener.universe import (
    POLYGON_BASE,
    FMP_BASE,
    SECTOR_OVERRIDES,
    _polygon_get,
    _fmp_get,
    _is_excluded_sic,
    _sic_to_sector,
    _fetch_adv_k,
)

logger = logging.getLogger(__name__)

_CACHE_PATH = Path(__file__).parent.parent.parent / ".short_universe_cache.json"
_CACHE_TTL_HOURS = 168  # 7 days — rebuilt weekly

_MIN_CAP_M = 2_000    # $2B
_MAX_CAP_M = 10_000   # $10B
_MIN_ADV_K = 500.0    # $500K ADV (borrowable float requirement)


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _load_cache() -> Optional[list[dict]]:
    try:
        if not _CACHE_PATH.exists():
            return None
        data = json.loads(_CACHE_PATH.read_text())
        cached_at = datetime.fromisoformat(data.get("cached_at", "2000-01-01"))
        if cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - cached_at).total_seconds() / 3600
        if age_hours > _CACHE_TTL_HOURS:
            logger.info("Short universe cache expired (%.1fh old)", age_hours)
            return None  # caller will trigger rebuild
        logger.info("Short universe cache hit (%.1fh old, %d tickers)", age_hours, len(data.get("tickers", [])))
        return data.get("tickers", [])
    except Exception as exc:
        logger.warning("Short universe cache read failed: %s", exc)
        return None


def _save_cache(tickers: list[dict]) -> None:
    try:
        payload = {
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "tickers": tickers,
        }
        _CACHE_PATH.write_text(json.dumps(payload, default=str))
        logger.info("Short universe cache saved (%d tickers)", len(tickers))
    except Exception as exc:
        logger.warning("Short universe cache save failed: %s", exc)


# ── Fetch logic ───────────────────────────────────────────────────────────────

def _fetch_wide_universe_fresh() -> list[dict]:
    """
    Full rebuild of the $2B–$10B supplementary universe.
    Takes ~5-10 minutes due to per-ticker FMP calls.
    """
    polygon_key = os.getenv("POLYGON_API_KEY")
    fmp_key = os.getenv("FMP_API_KEY")
    if not polygon_key or not fmp_key:
        logger.error("Short universe rebuild: missing POLYGON_API_KEY or FMP_API_KEY")
        return []

    # ── Step 1: Collect all tickers in $2B–$10B cap range ──────────────────
    target_exchanges = {"XNYS", "XNAS", "XASE"}
    all_symbols: list[str] = []

    next_url: Optional[str] = (
        f"{POLYGON_BASE}/v3/reference/tickers"
        f"?market=stocks&type=CS&active=true&limit=1000&apiKey={polygon_key}"
    )
    pages_fetched = 0
    while next_url and pages_fetched < 15:
        try:
            r = requests.get(next_url, timeout=20)
            pages_fetched += 1
        except Exception as exc:
            logger.warning("Wide universe page %d failed: %s", pages_fetched, exc)
            break
        if r.status_code == 429:
            time.sleep(10)
            continue
        if r.status_code != 200:
            break
        time.sleep(0.5)
        data = r.json()
        for t in data.get("results", []):
            ticker = t.get("ticker", "")
            exchange = t.get("primary_exchange", "")
            if exchange in target_exchanges and ticker:
                all_symbols.append(ticker)
        next_url_path = data.get("next_url")
        if next_url_path:
            sep = "&" if "?" in next_url_path else "?"
            next_url = f"{next_url_path}{sep}apiKey={polygon_key}"
        else:
            next_url = None

    # ── Step 2: Filter by market cap $2B–$10B + sector ─────────────────────
    from backend.screener.universe import _fetch_ticker_detail
    candidates: list[dict] = []

    for ticker in all_symbols:
        detail = _fetch_ticker_detail(ticker, polygon_key)
        time.sleep(0.25)
        if detail is None:
            continue
        mc = detail.get("market_cap")
        if mc is None:
            continue
        mktcap_m = mc / 1_000_000
        if not (_MIN_CAP_M <= mktcap_m <= _MAX_CAP_M):
            continue
        sic = detail.get("sic_code")
        if sic is not None and _is_excluded_sic(sic):
            continue
        sector = SECTOR_OVERRIDES.get(ticker) or _sic_to_sector(sic)
        if sector is None:
            continue
        candidates.append({"ticker": ticker, "market_cap_m": round(mktcap_m, 2), "sector": sector, "sic_code": sic})

    logger.info("Wide universe: %d candidates after cap/sector filter", len(candidates))

    # ── Step 3: ADV filter ──────────────────────────────────────────────────
    adv_qualified: list[dict] = []
    for c in candidates:
        adv = _fetch_adv_k(c["ticker"], polygon_key)
        time.sleep(0.12)
        if adv is not None and adv >= _MIN_ADV_K:
            c["adv_k"] = round(adv, 2)
            adv_qualified.append(c)

    logger.info("Wide universe: %d candidates after ADV filter", len(adv_qualified))

    # ── Step 4: FMP key-metrics-ttm (light scoring data) ───────────────────
    results: list[dict] = []
    for c in adv_qualified:
        ticker = c["ticker"]
        url = f"{FMP_BASE}/key-metrics-ttm?symbol={ticker}&limit=1&apikey={fmp_key}"
        r = _fmp_get(url)
        time.sleep(0.2)
        ev_revenue = ev_ebitda = gross_margin = revenue_growth = debt_to_equity = None
        is_profitable = None
        if r is not None and r.status_code == 200:
            try:
                km = r.json()
                if km:
                    m = km[0]
                    ev_revenue    = m.get("evToRevenueRatioTTM")
                    ev_ebitda     = m.get("evToEBITDATTM")
                    gross_margin  = m.get("grossProfitMarginTTM")
                    revenue_growth = m.get("revenueGrowthTTM")
                    debt_to_equity = m.get("debtToEquityTTM")
                    # EBITDA > 0 means profitable
                    ebitda = m.get("ebitdaTTM")
                    if ebitda is not None:
                        is_profitable = float(ebitda) > 0
            except Exception:
                pass
        results.append({
            "ticker":          ticker,
            "market_cap_m":    c["market_cap_m"],
            "sector":          c["sector"],
            "adv_k":           c.get("adv_k"),
            "ev_revenue":      ev_revenue,
            "ev_ebitda":       ev_ebitda,
            "gross_margin":    gross_margin,
            "revenue_growth":  revenue_growth,
            "debt_to_equity":  debt_to_equity,
            "is_profitable":   is_profitable,
        })

    return results


def _rebuild_cache_background() -> None:
    try:
        tickers = _fetch_wide_universe_fresh()
        if tickers:
            _save_cache(tickers)
    except Exception as exc:
        logger.exception("Wide universe background rebuild failed: %s", exc)


# ── Raw-factors adapter ───────────────────────────────────────────────────────

def _to_raw_factors(c: dict) -> dict:
    """
    Convert a wide-universe candidate dict to the raw_factors format expected
    by short_scorer.compute_short_scores(). Missing fields are None — the
    scorer handles them gracefully via neutral 5.0 fallback.
    """
    ev_multiple = c.get("ev_revenue") or c.get("ev_ebitda")  # prefer EV/Revenue for shorts
    return {
        "quality": {
            "raw_values": {
                "roic":               None,   # not available from key-metrics-ttm alone
                "fcf_conversion":     None,
                "gross_margin":       c.get("gross_margin"),
                "revenue_growth_yoy": c.get("revenue_growth"),
                "debt_to_equity":     c.get("debt_to_equity"),
                "eps_beat_rate":      None,
            }
        },
        "value": {
            "raw_values": {
                "ev_multiple":  ev_multiple,
                "p_fcf":        None,
                "price_book":   None,
                "is_profitable": c.get("is_profitable"),
            }
        },
        "beneish": {
            "gate_result": "INSUFFICIENT_DATA",
            "m_score": None,
        },
        "fmp": {
            "cash_runway_months": None,
        },
    }


# ── Public API ────────────────────────────────────────────────────────────────

def build_wide_universe(use_cache: bool = True) -> list[dict]:
    """
    Return the $2B–$10B supplementary universe for short scoring.

    Each returned dict has keys:
        ticker, market_cap_m, sector, adv_k, raw_factors, source,
        beneish_m_score (None), beneish_flag (None)

    Cache TTL is 7 days. On cache miss, spawns a background rebuild and returns
    an empty list so today's short screening uses only the watchlist source.
    On stale cache (expired), spawns background rebuild and returns stale data.
    """
    if not use_cache:
        tickers = _fetch_wide_universe_fresh()
        _save_cache(tickers)
    else:
        tickers = _load_cache()
        if tickers is None:
            # Cache miss or expired — trigger async rebuild, return empty for today
            logger.info("Wide universe cache miss — spawning background rebuild; today uses watchlist only")
            t = threading.Thread(target=_rebuild_cache_background, daemon=True)
            t.start()
            return []

    return [
        {
            "ticker":        c["ticker"],
            "market_cap_m":  c.get("market_cap_m"),
            "sector":        c.get("sector"),
            "adv_k":         c.get("adv_k"),
            "beneish_m_score": None,
            "beneish_flag":  None,
            "raw_factors":   _to_raw_factors(c),
            "source":        "wide_universe",
        }
        for c in tickers
    ]
