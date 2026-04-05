"""
Universe Builder — filters ~800 US micro/small-cap equities for daily screening.

Criteria:
  - Market cap: $50M–$2B
  - Sectors: SaaS/Tech (SIC 7371-7379), Healthcare (SIC 2830-2836, 5047, 5122, 8000-8099),
             Industrials (SIC 3400-3599, 3710-3799, 4800-4899)
  - ADV ≥ $500K (30-day Polygon OHLCV)
  - Analyst count ≤ 5 (yfinance)

Also provides fetch_ticker_data() — single coordinated fetch per ticker
returning all data needed by factor scorers. Called once per ticker;
result passed to all three factor scorers to avoid redundant API calls.

Rate limiting: 0.25s sleep between Polygon detail calls (sequential).
Polygon v3/reference/tickers LIST endpoint does not return market_cap or sic_code;
they are only available via the per-ticker DETAIL endpoint. Universe is cached to
.universe_cache.json (refreshed every 24 hours) to avoid re-fetching ~5000 detail
endpoints on every run.
"""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import requests
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

POLYGON_BASE = "https://api.polygon.io"

# Universe cache: avoids ~5000 Polygon detail API calls on every run.
# File lives at repo root; TTL is 24 hours.
_CACHE_PATH = Path(__file__).parent.parent.parent / ".universe_cache.json"
_CACHE_TTL_HOURS = 24

# Manual sector overrides: ticker → sector string
SECTOR_OVERRIDES: dict[str, str] = {}

# SIC code → sector mapping
_SIC_SAAS = set(range(7371, 7380))  # 7371–7379 inclusive
_SIC_HEALTHCARE = (
    set(range(2830, 2837))  # 2830–2836
    | {5047, 5122}
    | set(range(8000, 8100))  # 8000–8099
)
_SIC_INDUSTRIALS = (
    set(range(3400, 3600))   # 3400–3599
    | set(range(3710, 3800)) # 3710–3799
    | set(range(4800, 4900)) # 4800–4899
)

VALID_SECTORS = {"SaaS", "Healthcare", "Industrials"}


@dataclass
class UniverseCandidate:
    ticker: str
    market_cap_m: float       # market cap in $M
    sector: str               # 'SaaS' | 'Healthcare' | 'Industrials'
    adv_k: Optional[float] = None   # average daily volume in $K
    sic_code: Optional[int] = None
    analyst_count: Optional[int] = None


def _sic_to_sector(sic: Optional[int]) -> Optional[str]:
    if sic is None:
        return None
    if sic in _SIC_SAAS:
        return "SaaS"
    if sic in _SIC_HEALTHCARE:
        return "Healthcare"
    if sic in _SIC_INDUSTRIALS:
        return "Industrials"
    return None


def _polygon_get(url: str, params: dict, max_retries: int = 3) -> Optional[requests.Response]:
    """
    GET wrapper with exponential backoff on HTTP 429 (rate limit).
    Returns the Response on 200, None on failure.
    """
    for attempt in range(max_retries):
        try:
            r = requests.get(url, params=params, timeout=15)
            if r.status_code == 200:
                return r
            if r.status_code == 429:
                backoff = 15 * (attempt + 1)  # 15s, 30s, 45s
                logger.debug("Polygon 429 on %s (attempt %d) — backing off %ds", url.split("?")[0][-40:], attempt + 1, backoff)
                time.sleep(backoff)
                continue
            return None
        except Exception as exc:
            logger.debug("Polygon request failed (attempt %d): %s", attempt + 1, exc)
            if attempt < max_retries - 1:
                time.sleep(5)
    return None


def _fetch_adv_k(ticker: str, polygon_key: str) -> Optional[float]:
    """
    Compute 30-day average daily dollar volume using Polygon aggregate bars.
    Returns value in $K, or None on failure.
    """
    try:
        today = date.today()
        from_date = (today - timedelta(days=45)).strftime("%Y-%m-%d")  # 45-day window to ensure 30 trading days
        to_date = today.strftime("%Y-%m-%d")
        r = _polygon_get(
            f"{POLYGON_BASE}/v2/aggs/ticker/{ticker}/range/1/day/{from_date}/{to_date}",
            params={
                "adjusted": "true",
                "sort": "asc",
                "limit": 30,
                "apiKey": polygon_key,
            },
        )
        if r is None:
            return None
        bars = r.json().get("results", [])
        if not bars:
            return None
        # Dollar volume = close × volume
        dv_list = [b.get("c", 0) * b.get("v", 0) for b in bars if b.get("c") and b.get("v")]
        if not dv_list:
            return None
        adv = sum(dv_list) / len(dv_list)
        return round(adv / 1000, 1)  # convert to $K
    except Exception as exc:
        logger.debug("ADV fetch failed for %s: %s", ticker, exc)
        return None


def _fetch_analyst_count(ticker: str) -> Optional[int]:
    """Return analyst count from yfinance, or None on failure."""
    try:
        info = yf.Ticker(ticker).info or {}
        return info.get("numberOfAnalystOpinions")
    except Exception as exc:
        logger.debug("%s: analyst count fetch failed: %s", ticker, exc)
        return None


def _fetch_ticker_detail(ticker: str, polygon_key: str) -> Optional[dict]:
    """
    Fetch market_cap and sic_code from the Polygon per-ticker detail endpoint.
    The v3/reference/tickers LIST endpoint does not return these fields;
    they are only available via the individual detail endpoint.

    Retries up to 3 times on 429 (rate-limit) responses with exponential backoff.
    Returns {"market_cap": float, "sic_code": int|None} or None on failure.
    """
    for attempt in range(3):
        try:
            r = requests.get(
                f"{POLYGON_BASE}/v3/reference/tickers/{ticker}",
                params={"apiKey": polygon_key},
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json().get("results", {})
                mc = data.get("market_cap")
                sic = data.get("sic_code")
                return {
                    "market_cap": float(mc) if mc is not None else None,
                    "sic_code": int(sic) if sic and str(sic).isdigit() else None,
                }
            if r.status_code == 429:
                backoff = 5 * (attempt + 1)
                logger.debug("%s: 429 rate limit (attempt %d) — backing off %ds", ticker, attempt + 1, backoff)
                time.sleep(backoff)
                continue
            # Other non-200 status: give up
            return None
        except Exception as exc:
            logger.debug("%s: detail fetch failed (attempt %d): %s", ticker, attempt + 1, exc)
            if attempt < 2:
                time.sleep(2)
    return None


def _load_universe_cache() -> Optional[list["UniverseCandidate"]]:
    """Return cached universe if it exists and is < 24 hours old, else None."""
    if not _CACHE_PATH.exists():
        return None
    age_hours = (time.time() - _CACHE_PATH.stat().st_mtime) / 3600
    if age_hours > _CACHE_TTL_HOURS:
        logger.info("Universe cache is %.1f hours old — rebuilding", age_hours)
        return None
    try:
        rows = json.loads(_CACHE_PATH.read_text())
        universe = [UniverseCandidate(**row) for row in rows]
        logger.info("Loaded %d candidates from universe cache (%.1fh old)", len(universe), age_hours)
        return universe
    except Exception as exc:
        logger.warning("Failed to read universe cache: %s — rebuilding", exc)
        return None


def _save_universe_cache(universe: list["UniverseCandidate"]) -> None:
    """Persist universe to disk cache."""
    try:
        rows = [
            {
                "ticker":        c.ticker,
                "market_cap_m":  c.market_cap_m,
                "sector":        c.sector,
                "adv_k":         c.adv_k,
                "sic_code":      c.sic_code,
                "analyst_count": c.analyst_count,
            }
            for c in universe
        ]
        _CACHE_PATH.write_text(json.dumps(rows))
        logger.info("Universe cached to %s (%d entries)", _CACHE_PATH.name, len(rows))
    except Exception as exc:
        logger.warning("Failed to write universe cache: %s", exc)


def build_universe(use_cache: bool = True) -> list[UniverseCandidate]:
    """
    Build the screener universe from Polygon reference tickers.

    Filters:
      - US exchange (NYSE/NASDAQ/AMEX)
      - Market cap $50M–$2B
      - Valid SIC sector (SIC codes, not yfinance sectors)
      - ADV ≥ $500K
      - Analyst count ≤ 5

    Returns up to ~800 UniverseCandidate objects.

    Caching: Results are cached to .universe_cache.json for 24 hours. Pass
    use_cache=False to force a full rebuild (e.g., for CI or first-time setup).

    Implementation note: The Polygon v3/reference/tickers LIST endpoint does not
    return market_cap or sic_code (they are always None). Phase 1 collects all
    common-stock symbols; Phase 2 fetches individual detail pages sequentially
    with 0.25s sleep to respect rate limits and avoid 429s.
    """
    polygon_key = os.getenv("POLYGON_API_KEY")
    if not polygon_key:
        raise RuntimeError("POLYGON_API_KEY not set")

    # ── Cache check ────────────────────────────────────────────────────────────
    if use_cache:
        cached = _load_universe_cache()
        if cached is not None:
            return cached

    # ── Phase 1: Collect all common-stock ticker symbols on target exchanges ────
    # The list endpoint only gives us ticker + exchange (no market_cap/sic_code).
    all_symbols: list[str] = []
    target_exchanges = {"XNYS", "XNAS", "XASE"}

    next_url: Optional[str] = (
        f"{POLYGON_BASE}/v3/reference/tickers"
        f"?market=stocks&type=CS&active=true&limit=1000&apiKey={polygon_key}"
    )

    pages_fetched = 0
    while next_url and pages_fetched < 15:  # safety cap (~15K symbols max)
        try:
            r = requests.get(next_url, timeout=20)
            pages_fetched += 1
        except Exception as exc:
            logger.warning("Polygon ticker list page %d failed: %s", pages_fetched, exc)
            break

        if r.status_code == 429:
            logger.warning("Polygon ticker list 429 on page %d — waiting 10s", pages_fetched)
            time.sleep(10)
            continue
        if r.status_code != 200:
            logger.warning("Polygon ticker list HTTP %d on page %d", r.status_code, pages_fetched)
            break

        time.sleep(0.5)  # conservative: 2 pages/sec for the list endpoint

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

    logger.info(
        "Polygon list: %d common-stock symbols on NYSE/NASDAQ/AMEX (%d pages)",
        len(all_symbols), pages_fetched,
    )

    # ── Phase 2: Sequential detail-fetch for market_cap + sic_code ───────────
    # Sequential (not parallel) to stay within Polygon rate limits.
    # 0.25s between calls = ~4 req/sec. For ~5000 symbols: ~20 minutes.
    # This phase is the bottleneck; the result is cached for 24 hours.
    candidates: list[UniverseCandidate] = []
    logger.info("Fetching detail for %d symbols (sequential, ~0.25s each — this takes ~20 min)", len(all_symbols))

    for i, ticker in enumerate(all_symbols):
        detail = _fetch_ticker_detail(ticker, polygon_key)
        time.sleep(0.25)

        if detail is None:
            continue
        mc = detail.get("market_cap")
        if mc is None:
            continue
        mktcap_m = mc / 1_000_000
        if not (50 <= mktcap_m <= 2000):
            continue
        sic = detail.get("sic_code")
        sector = SECTOR_OVERRIDES.get(ticker) or _sic_to_sector(sic)
        if sector is None:
            continue
        candidates.append(UniverseCandidate(
            ticker=ticker,
            market_cap_m=round(mktcap_m, 2),
            sector=sector,
            sic_code=sic,
        ))

        if (i + 1) % 500 == 0:
            logger.info(
                "  Detail fetch progress: %d/%d symbols, %d candidates so far",
                i + 1, len(all_symbols), len(candidates),
            )

    logger.info("Polygon reference: %d sector-qualified candidates before ADV/analyst filter", len(candidates))

    # ── ADV filter (parallel) ─────────────────────────────────────────────────
    def _check_adv(cand: UniverseCandidate) -> Optional[UniverseCandidate]:
        adv = _fetch_adv_k(cand.ticker, polygon_key)
        if adv is None or adv < 500:
            return None
        cand.adv_k = adv
        return cand

    adv_qualified: list[UniverseCandidate] = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_check_adv, c): c for c in candidates}
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                adv_qualified.append(result)

    logger.info("After ADV ≥ $500K filter: %d candidates", len(adv_qualified))

    # ── Analyst count filter (parallel, yfinance) ─────────────────────────────
    def _check_analyst(cand: UniverseCandidate) -> Optional[UniverseCandidate]:
        count = _fetch_analyst_count(cand.ticker)
        cand.analyst_count = count
        # Allow through if count is None (data unavailable) or ≤ 5
        if count is not None and count > 5:
            return None
        return cand

    final: list[UniverseCandidate] = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_check_analyst, c): c for c in adv_qualified}
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                final.append(result)

    logger.info("Final universe after analyst ≤ 5 filter: %d candidates", len(final))

    # ── Cache the final universe ───────────────────────────────────────────────
    if final:
        _save_universe_cache(final)

    return final


def fetch_ticker_data(ticker: str) -> dict:
    """
    Single coordinated data fetch for a ticker. Called once per ticker;
    result is passed to all factor scorers to avoid redundant API calls.

    Returns:
        {
            "ticker":              str,
            "fmp":                 dict,   # output of fetch_fmp()
            "polygon_financials":  dict,   # raw Polygon /vX/reference/financials (limit=4, FY+TTM)
            "price_history":       list,   # daily OHLCV dicts (13 months), sorted oldest→newest
            "yf_info":             dict,   # yfinance Ticker.info
        }
    Never raises — partial failures return empty sub-dicts.
    """
    from backend.fetchers.fmp_fetcher import fetch_fmp  # avoid circular at module level

    result: dict = {
        "ticker":             ticker.upper(),
        "fmp":                {},
        "polygon_financials": {"results": []},
        "price_history":      [],
        "yf_info":            {},
    }

    # ── fetch_fmp (yfinance + Polygon balance sheet) ─────────────────────────
    try:
        result["fmp"] = fetch_fmp(ticker)
    except Exception as exc:
        logger.warning("%s: fetch_fmp failed: %s", ticker, exc)

    # ── Polygon financials: two separate calls merged into polygon_financials ──
    # Annual call (timeframe=annual, limit=2): provides FY rows for Beneish /
    # Quality / Value factor scorers. Polygon defaults to quarterly + TTM, so
    # without timeframe=annual, no FY rows are returned.
    # TTM call (timeframe=ttm, limit=1): provides TTM OCF for Value scorer and
    # cash runway (also fetched by fmp_fetcher, but kept here for consistency).
    polygon_key = os.getenv("POLYGON_API_KEY")
    if polygon_key:
        merged_results: list[dict] = []
        for timeframe, limit in [("annual", 2), ("ttm", 1)]:
            r = _polygon_get(
                f"{POLYGON_BASE}/vX/reference/financials",
                params={
                    "ticker": ticker,
                    "timeframe": timeframe,
                    "limit": limit,
                    "apiKey": polygon_key,
                },
            )
            if r is not None:
                rows = r.json().get("results", [])
                if timeframe == "annual":
                    for row in rows:
                        row["fiscal_period"] = "FY"
                merged_results.extend(rows)
            else:
                logger.warning("%s: Polygon financials (%s) failed or rate-limited", ticker, timeframe)
        result["polygon_financials"] = {"results": merged_results}

    # ── Polygon price history (13 months for 12-1 momentum) ──────────────────
    if polygon_key:
        try:
            import datetime
            end_date   = datetime.date.today().isoformat()
            start_date = (datetime.date.today() - datetime.timedelta(days=400)).isoformat()
            r = _polygon_get(
                f"{POLYGON_BASE}/v2/aggs/ticker/{ticker}/range/1/day/{start_date}/{end_date}",
                params={"adjusted": "true", "sort": "asc", "limit": 500, "apiKey": polygon_key},
            )
            if r is not None:
                bars = r.json().get("results", [])
                result["price_history"] = [
                    {"date": b.get("t"), "open": b.get("o"), "high": b.get("h"),
                     "low": b.get("l"), "close": b.get("c"), "volume": b.get("v")}
                    for b in bars
                ]
        except Exception as exc:
            logger.warning("%s: Polygon price history failed: %s", ticker, exc)

    # ── yfinance info (analyst count, earnings history, sector) ──────────────
    try:
        t = yf.Ticker(ticker)
        result["yf_info"] = t.info or {}

        # Augment with earningsHistory for eps_beat_rate
        try:
            eh = t.earnings_history
            if eh is not None and not eh.empty:
                eh_list = []
                for _, row in eh.iterrows():
                    eh_list.append({
                        "epsEstimate": row.get("epsEstimate"),
                        "epsActual":   row.get("epsActual"),
                    })
                result["yf_info"]["earningsHistory"] = eh_list
        except Exception as exc:
            logger.debug("%s: earningsHistory fetch failed: %s", ticker, exc)
    except Exception as exc:
        logger.warning("%s: yfinance info failed: %s", ticker, exc)

    return result
