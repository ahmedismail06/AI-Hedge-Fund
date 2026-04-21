"""
Universe Builder — filters ~800 US micro/small-cap equities for daily screening.

Criteria:
  - Market cap: $50M–$2B
  - Sectors: SaaS/Tech (SIC 7371-7379), Healthcare (SIC 2830-2836, 5047, 5122, 8000-8099),
             Industrials (SIC 3400-3599, 3710-3799, 4800-4899)
  - ADV ≥ $500K (30-day Polygon OHLCV)
  - Analyst count ≤ 5 (Financial Modeling Prep)

Also provides fetch_ticker_data() — single coordinated fetch per ticker
returning all data needed by factor scorers. Called once per ticker;
result passed to all three factor scorers to avoid redundant API calls.

Rate limiting: Proactive sleeps and exponential backoff are heavily utilized 
to respect Polygon's limits and FMP's 300 req/min limits.
"""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

POLYGON_BASE = "https://api.polygon.io"
FMP_BASE = "https://financialmodelingprep.com/stable"

# Universe cache: avoids ~5000 Polygon detail API calls on every run.
# File lives at repo root; TTL is 24 hours.
_CACHE_PATH = Path(__file__).parent.parent.parent / ".universe_cache.json"
_CACHE_TTL_HOURS = 24

# Manual sector overrides: ticker → sector string
SECTOR_OVERRIDES: Dict[str, str] = {}

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
    market_cap_m: float             # market cap in $M
    sector: str                     # 'SaaS' | 'Healthcare' | 'Industrials'
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


def _fmp_get(url: str, max_retries: int = 3) -> Optional[requests.Response]:
    """
    GET wrapper for FMP with exponential backoff to handle the 300 req/min limit.
    """
    for attempt in range(max_retries):
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                return r
            if r.status_code == 429:
                backoff = 6.0 * (attempt + 1)  # 6s, 12s, 18s
                logger.debug("FMP 429 on %s (attempt %d) — backing off %ds", url.split("?")[0][-40:], attempt + 1, backoff)
                time.sleep(backoff)
                continue
            return None
        except Exception as exc:
            logger.debug("FMP request failed (attempt %d): %s", attempt + 1, exc)
            if attempt < max_retries - 1:
                time.sleep(2)
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


def _fetch_analyst_count(ticker: str, fmp_key: str) -> Optional[int]:
    """Return analyst count from FMP, or None on failure."""
    try:
        url = f"{FMP_BASE}/analyst-estimates?symbol={ticker}&period=annual&limit=1&apikey={fmp_key}"
        r = _fmp_get(url)
        if r is not None:
            data = r.json()
            if data and isinstance(data, list):
                return data[0].get("numberAnalystEstimatedRevenue")
        return None
    except Exception as exc:
        logger.debug("%s: FMP analyst count fetch failed: %s", ticker, exc)
        return None


def _fetch_ticker_detail(ticker: str, polygon_key: str) -> Optional[Dict[str, Any]]:
    """
    Fetch market_cap and sic_code from the Polygon per-ticker detail endpoint.
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
            return None
        except Exception as exc:
            logger.debug("%s: detail fetch failed (attempt %d): %s", ticker, attempt + 1, exc)
            if attempt < 2:
                time.sleep(2)
    return None


def _load_universe_cache() -> Optional[List[UniverseCandidate]]:
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


def _save_universe_cache(universe: List[UniverseCandidate]) -> None:
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


def build_universe(use_cache: bool = True) -> List[UniverseCandidate]:
    """
    Build the screener universe from Polygon reference tickers.
    Filters: US exchange, Cap $50M–$2B, Valid SIC, ADV ≥ $500K, Analyst ≤ 5.
    """
    polygon_key = os.getenv("POLYGON_API_KEY")
    fmp_key = os.getenv("FMP_API_KEY")
    
    if not polygon_key:
        raise RuntimeError("POLYGON_API_KEY not set")
    if not fmp_key:
        raise RuntimeError("FMP_API_KEY not set")

    if use_cache:
        cached = _load_universe_cache()
        if cached is not None:
            return cached

    # ── Phase 1: Collect all common-stock ticker symbols ────
    all_symbols: List[str] = []
    target_exchanges = {"XNYS", "XNAS", "XASE"}

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
            logger.warning("Polygon ticker list page %d failed: %s", pages_fetched, exc)
            break

        if r.status_code == 429:
            logger.warning("Polygon ticker list 429 on page %d — waiting 10s", pages_fetched)
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

    logger.info("Polygon list: %d common-stock symbols on NYSE/NASDAQ/AMEX (%d pages)", len(all_symbols), pages_fetched)

    # ── Phase 2: Sequential detail-fetch for market_cap + sic_code ───────────
    candidates: List[UniverseCandidate] = []
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
            logger.info("  Detail fetch progress: %d/%d symbols, %d candidates so far", i + 1, len(all_symbols), len(candidates))

    # ── ADV filter (parallel) ─────────────────────────────────────────────────
    def _check_adv(cand: UniverseCandidate) -> Optional[UniverseCandidate]:
        adv = _fetch_adv_k(cand.ticker, polygon_key)
        if adv is None or adv < 500:
            return None
        cand.adv_k = adv
        return cand

    adv_qualified: List[UniverseCandidate] = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_check_adv, c): c for c in candidates}
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                adv_qualified.append(result)

    # ── Analyst count filter (parallel, FMP) ─────────────────────────────
    def _check_analyst(cand: UniverseCandidate) -> Optional[UniverseCandidate]:
        count = _fetch_analyst_count(cand.ticker, fmp_key)
        cand.analyst_count = count
        time.sleep(0.5)  # Proactive pacing to protect FMP 300 req/min
        
        if count is not None and count > 5:
            return None
        return cand

    final: List[UniverseCandidate] = []
    # Reduced max_workers to 3 to safely coast under FMP API limits
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_check_analyst, c): c for c in adv_qualified}
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                final.append(result)

    logger.info("Final universe after analyst ≤ 5 filter: %d candidates", len(final))

    if final:
        _save_universe_cache(final)

    return final


def _polygon_roe(polygon_financials: dict) -> Optional[float]:
    """
    Compute ROE from Polygon annual financials.
    Mirrors the logic in quality.py so the pre-filter uses the same data source
    as the scorer when FMP income/balance data is unavailable.
    """
    results = polygon_financials.get("results", [])
    fy_rows = [r for r in results if r.get("fiscal_period") == "FY"]
    fy_rows.sort(key=lambda r: r.get("filing_date", ""), reverse=True)
    if not fy_rows:
        return None
    fin = fy_rows[0].get("financials", {})
    inc = fin.get("income_statement", {})
    bs  = fin.get("balance_sheet", {})

    def _v(stmt: dict, key: str) -> Optional[float]:
        val = stmt.get(key, {})
        return val.get("value") if isinstance(val, dict) else val

    net_income = _v(inc, "net_income_loss")
    equity     = _v(bs,  "equity")
    if net_income is not None and equity and equity != 0:
        return net_income / equity
    return None


def filter_by_profitability(universe: List[UniverseCandidate], raw_data_map: Dict[str, dict]) -> List[UniverseCandidate]:
    """
    Exclude tickers with negative ROE, pre-revenue biotech signature, or insufficient data.
    Runs after data fetch but before scoring.

    ROE source priority:
      1. FMP income_statement + balance_sheet (fetch_quality_fmp_batch output)
      2. Polygon annual financials (always fetched in fetch_ticker_data)
    If FMP data is absent (no FMP_API_KEY or API failure), the Polygon fallback
    ensures negative-ROE tickers are still caught.
    """
    filtered: List[UniverseCandidate] = []
    exclusions = {
        "NEGATIVE_ROE": 0,
        "PRE_REVENUE_BIOTECH": 0,
        "INSUFFICIENT_QUALITY_DATA": 0
    }
    # Track per-ticker ROE for diagnostic log
    roe_map: dict[str, Optional[float]] = {}

    for cand in universe:
        ticker = cand.ticker
        data = raw_data_map.get(ticker, {})

        fmp_quality = data.get("fmp", {})
        fmp_inc = fmp_quality.get("income_statement", [])
        fmp_bs = fmp_quality.get("balance_sheet", [])

        # ROE — FMP primary, Polygon fallback
        roe: Optional[float] = None
        roe_source = "none"
        if fmp_inc and fmp_bs:
            net_inc = fmp_inc[0].get("netIncome")
            equity  = fmp_bs[0].get("totalStockholdersEquity")
            if net_inc is not None and equity and equity != 0:
                roe = net_inc / equity
                roe_source = "fmp"

        if roe is None:
            polygon_roe = _polygon_roe(data.get("polygon_financials", {}))
            if polygon_roe is not None:
                roe = polygon_roe
                roe_source = "polygon"

        roe_map[ticker] = roe
        logger.debug("%s: ROE=%.3f (source=%s)", ticker, roe if roe is not None else float("nan"), roe_source)

        # Gross Margin check
        gm: Optional[float] = None
        if fmp_inc:
            rev = fmp_inc[0].get("revenue")
            gp  = fmp_inc[0].get("grossProfit")
            if rev and rev != 0 and gp is not None:
                gm = gp / rev

        # Revenue Growth check
        rev_growth: Optional[float] = None
        if len(fmp_inc) >= 2:
            r1 = fmp_inc[0].get("revenue")
            r2 = fmp_inc[1].get("revenue")
            if r1 is not None and r2 and r2 != 0:
                rev_growth = (r1 - r2) / abs(r2)

        # 1. Negative ROE
        if roe is not None and roe < 0:
            exclusions["NEGATIVE_ROE"] += 1
            logger.info("%s: Excluded — NEGATIVE_ROE (%.3f, source=%s)", ticker, roe, roe_source)
            continue

        # 2. Pre-revenue biotech signature
        if roe is None and gm is not None and gm > 0.95:
            exclusions["PRE_REVENUE_BIOTECH"] += 1
            logger.info("%s: Excluded — PRE_REVENUE_BIOTECH (gm=%.3f)", ticker, gm)
            continue

        # 3. Insufficient data
        if gm is None and rev_growth is None:
            exclusions["INSUFFICIENT_QUALITY_DATA"] += 1
            logger.info("%s: Excluded — INSUFFICIENT_QUALITY_DATA", ticker)
            continue

        filtered.append(cand)

    excluded_count = sum(exclusions.values())
    logger.info(
        "Pre-filter removed %d tickers. Remaining: %d tickers. Breakdown: %s",
        excluded_count,
        len(filtered),
        {r: c for r, c in exclusions.items() if c > 0},
    )
    # Diagnostic: first 5 remaining tickers with their ROE values
    sample = [
        f"{c.ticker}(ROE={'%.3f' % roe_map[c.ticker] if roe_map.get(c.ticker) is not None else 'None'})"
        for c in filtered[:5]
    ]
    logger.info("First 5 remaining: %s", sample)

    return filtered


def fetch_ticker_data(ticker: str) -> dict:
    """
    Single coordinated data fetch for a ticker.
    Now utilizes FMP instead of yfinance for soft-factor mapping.
    """
    from backend.fetchers.fmp_fetcher import fetch_fmp 

    result: Dict[str, Any] = {
        "ticker":             ticker.upper(),
        "fmp":                {},
        "polygon_financials": {"results": []},
        "price_history":      [],
        "yf_info":            {},  # Dict preserved for downstream compatibility
    }

    # ── fetch_fmp (core financial statements) ─────────────────────────
    try:
        result["fmp"] = fetch_fmp(ticker)
    except Exception as exc:
        logger.warning("%s: fetch_fmp failed: %s", ticker, exc)

    # ── Polygon financials ───────────────────────────────────────────────────
    polygon_key = os.getenv("POLYGON_API_KEY")
    if polygon_key:
        merged_results: List[Dict[str, Any]] = []
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

    # ── Polygon price history ────────────────────────────────────────────────
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

    # ── FMP info (replaces yfinance soft metrics mapping) ────────────────────
    fmp_key = os.getenv("FMP_API_KEY")
    if fmp_key:
        try:
            # 1. Earnings Surprises (replaces yf.earnings_history)
            r_earnings = _fmp_get(f"{FMP_BASE}/earnings-surprises?symbol={ticker}&apikey={fmp_key}")
            if r_earnings is not None:
                eh_data = r_earnings.json()
                if eh_data and isinstance(eh_data, list):
                    eh_list = []
                    for row in eh_data[:4]:
                        eh_list.append({
                            "epsEstimate": row.get("estimatedEarning"),
                            "epsActual":   row.get("actualEarning"),
                        })
                    result["yf_info"]["earningsHistory"] = eh_list

            # 2. Forward Estimates (replaces yf.info forwardEps)
            r_estimates = _fmp_get(f"{FMP_BASE}/analyst-estimates?symbol={ticker}&period=annual&limit=2&apikey={fmp_key}")
            if r_estimates is not None:
                est_data = r_estimates.json()
                if est_data and isinstance(est_data, list) and len(est_data) > 0:
                    result["yf_info"]["forwardEps"] = est_data[0].get("estimatedEpsAvg")
                    
            # 3. Key Metrics TTM (replaces yf.info pegRatio)
            r_metrics = _fmp_get(f"{FMP_BASE}/key-metrics-ttm?symbol={ticker}&limit=1&apikey={fmp_key}")
            if r_metrics is not None:
                metrics_data = r_metrics.json()
                if metrics_data and isinstance(metrics_data, list) and len(metrics_data) > 0:
                    result["yf_info"]["pegRatio"] = metrics_data[0].get("pegRatioTTM")
                    
            # Critical pacing to ensure the 3 workers hitting 3 endpoints each
            # do not exceed FMP's 300 req/min global limit
            time.sleep(1.5) 
            
        except Exception as exc:
            logger.warning("%s: FMP info fetch failed: %s", ticker, exc)

    return result