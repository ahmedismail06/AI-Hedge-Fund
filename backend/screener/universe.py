"""
Universe Builder — filters ~800 US micro/small-cap equities for daily screening.

Criteria:
  - Market cap: $50M–$2B
  - Sectors: broad US equities minus exclusions.
             Excluded: Pharma/Biotech R&D (SIC 2830-2836, 8731), Mining/Metals (1000-1499,
             3300-3399), Oil & Gas Exploration (1300-1389), Financial Services (6000-6411,
             6700-6799), Utilities (4900-4999).
             Kept sectors: SaaS, Healthcare (non-pharma), Industrials, Consumer,
             Real Estate (operating cos), Other.
  - ADV ≥ $500K (30-day Polygon OHLCV)
  - Analyst count ≤ 10 (Financial Modeling Prep)

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

# Gate 1 — BROKEN_UNIT_ECONOMICS: hard GM floor by sector.
# Excludes companies selling below cost. Calibrated to each sector's structural margin profile
# so legitimate low-margin businesses (trucking, distribution) are not penalised.
_GM_FLOOR_BROKEN: Dict[str, float] = {
    "SaaS":        0.15,  # sub-15% in software/hardware is irreparable at the product level
    "Healthcare":  0.10,  # allows medical distributors (15–25% GM) while catching broken cases
    "Industrials": 0.05,  # allows trucking (5–12% GM) and contract services (8–15% GM)
    "Consumer":    0.05,  # allows electronics retail (5–15% GM) and wholesale distribution
    "Real Estate": 0.05,  # operating cos have varied revenue recognition
    "Other":       0.05,  # conservative catch-all
}

# SIC ranges excluded from the universe (inclusive on both ends).
# Order matters only for readability — all ranges are checked.
_EXCLUDED_SIC_RANGES = [
    (1000, 1499),   # Mining (metal/coal), oil & gas extraction, nonmetallic minerals
    (2830, 2836),   # Pharma & drug manufacturing (pre-revenue biotech / pharma R&D)
    (2860, 2899),   # Industrial organic / agricultural / misc chemicals (Basic Materials)
    (2900, 2999),   # Petroleum refining and related products (Energy)
    (3300, 3399),   # Primary metals industries (steel mills, aluminum smelters)
    (4900, 4999),   # Utilities (electric, gas, sanitary services)
    (6000, 6199),   # Banks, savings institutions, credit companies
    (6200, 6289),   # Security & commodity brokers/dealers
    (6300, 6411),   # Insurance carriers and agents
    (6700, 6799),   # Investment holding companies and REITs
]
# Point exclusions not covered by the ranges above
_EXCLUDED_SIC_POINT = frozenset({8731})  # Commercial physical & biological research (biotech R&D)

VALID_SECTORS = {"SaaS", "Healthcare", "Industrials", "Consumer", "Real Estate", "Other"}

# ── FMP sector backstop ───────────────────────────────────────────────────────
# Polygon's SIC field is null or wrong for a non-trivial share of tickers
# (notably mortgage REITs classified as 6500 instead of 6798). FMP's /profile
# `sector` is the authoritative cross-check.
_FMP_EXCLUDED_SECTORS = frozenset({
    "Real Estate",          # all REITs (mortgage, equity, hybrid)
    "Financial Services",   # FMP's umbrella for banks/insurers/brokers
    "Energy",               # oil & gas exploration / production / services
    "Utilities",
    "Basic Materials",      # mining, metals, paper, chemicals
})

# FMP sector strings → internal universe label.
# Categories not listed (Real Estate, Financial Services, Energy, Utilities,
# Basic Materials) are excluded above. Healthcare goes into Healthcare even
# though pharma R&D is excluded by SIC 2830-2836 + 8731 (that's the load-bearing
# pharma filter; FMP's "Healthcare" is broader and includes med devices/services
# we want to keep).
_FMP_SECTOR_TO_INTERNAL: Dict[str, str] = {
    "Technology":             "SaaS",
    "Communication Services": "SaaS",
    "Healthcare":             "Healthcare",
    "Industrials":            "Industrials",
    "Consumer Cyclical":      "Consumer",
    "Consumer Defensive":     "Consumer",
}


@dataclass
class UniverseCandidate:
    ticker: str
    market_cap_m: float             # market cap in $M
    sector: str                     # 'SaaS' | 'Healthcare' | 'Industrials' | 'Consumer' | 'Real Estate' | 'Other'
    adv_k: Optional[float] = None   # average daily volume in $K
    sic_code: Optional[int] = None
    analyst_count: Optional[int] = None


def _is_excluded_sic(sic: Optional[int]) -> bool:
    """Excludes by SIC range/point. None SICs are NOT excluded here — the caller
    must apply the FMP sector backstop instead of treating null as 'unknown OK'."""
    if sic is None:
        return False
    if sic in _EXCLUDED_SIC_POINT:
        return True
    return any(lo <= sic <= hi for lo, hi in _EXCLUDED_SIC_RANGES)


def _resolve_sector_with_fmp_backstop(
    ticker: str,
    sic: Optional[int],
    fmp_key: str,
) -> Optional[str]:
    """
    Authoritative sector classification with FMP cross-check.

    Returns None when the ticker should be excluded from the universe.

    Decision tree:
      1. SECTOR_OVERRIDES (manual map) wins absolutely.
      2. Try SIC-based mapping.
      3. If SIC said None, or mapped to ambiguous "Real Estate"/"Other"
         (REITs commonly misclassified as SIC 6500-6552), consult FMP /profile.
      4. If FMP says excluded sector → exclude (FMP wins on exclusion).
      5. If SIC was None and FMP gave a usable sector → use FMP's mapping.
      6. If neither source produces a sector → exclude.
    """
    override = SECTOR_OVERRIDES.get(ticker)
    if override:
        return override

    # Short-circuit on explicit SIC exclusion (pharma R&D, banks, REITs, etc.) —
    # no need to spend an FMP /profile call on something we already know we
    # reject from the universe.
    if sic is not None and _is_excluded_sic(sic):
        return None

    sic_sector = _sic_to_sector(sic)

    # Only call FMP when SIC mapping is null OR ambiguous (Real Estate / Other).
    # Real Estate is ambiguous because SIC 6500-6552 covers both legit operators
    # and misclassified REITs; FMP correctly tags REITs as "Real Estate" and we
    # exclude that. "Other" is ambiguous because SIC outside our positive ranges
    # could be anything.
    if sic_sector in (None, "Real Estate", "Other"):
        fmp_sector = _fetch_fmp_sector(ticker, fmp_key)
        if fmp_sector in _FMP_EXCLUDED_SECTORS:
            return None
        if sic_sector is None and fmp_sector:
            return _FMP_SECTOR_TO_INTERNAL.get(fmp_sector)

    return sic_sector


def _fetch_fmp_profile(ticker: str, fmp_key: str) -> dict:
    """Return FMP profile fields (sector, country) from /profile, or {} on miss."""
    try:
        r = _fmp_get(f"{FMP_BASE}/profile?symbol={ticker}&apikey={fmp_key}")
        if r is None:
            return {}
        data = r.json()
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return {
                "sector":  data[0].get("sector") or None,
                "country": data[0].get("country") or None,
            }
    except Exception:
        pass
    return {}


def _fetch_fmp_sector(ticker: str, fmp_key: str) -> Optional[str]:
    """Return FMP's `sector` string from /profile, or None on miss."""
    return _fetch_fmp_profile(ticker, fmp_key).get("sector")


def _sic_to_sector(sic: Optional[int]) -> Optional[str]:
    """Map a SIC code to a sector label, or None if the ticker is excluded from the universe."""
    if sic is None:
        return None
    if _is_excluded_sic(sic):
        return None
    # Positive sector assignments (first match wins)
    if 7371 <= sic <= 7379 or 3571 <= sic <= 3579 or 3671 <= sic <= 3679:
        return "SaaS"       # software, IT services, computer/electronic hardware
    if (8000 <= sic <= 8099) or (3841 <= sic <= 3851) or sic in {5047, 5122, 3826, 3827}:
        return "Healthcare"  # health services, medical devices, instruments, distributors
    if (3400 <= sic <= 3599) or (3710 <= sic <= 3799) or (4000 <= sic <= 4899):
        return "Industrials"  # fabricated metals, machinery, transport equipment, comms
    if (5000 <= sic <= 5999) or (2000 <= sic <= 2829) or (2837 <= sic <= 2859):
        return "Consumer"    # retail, wholesale, food/textiles + consumer chemicals (soaps,
                             # cosmetics, paints). Industrial chemicals (2860-2899) and
                             # petroleum (2900-2999) excluded above.
    if 6500 <= sic <= 6552:
        return "Real Estate"  # operating companies only (not REITs — those are excluded above)
    return "Other"


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
    Fetch market_cap, sic_code, and locale from the Polygon per-ticker detail endpoint.
    locale == "us" means US domestic issuer (files 10-K); anything else is a foreign filer.
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
                    "locale": data.get("locale"),
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
    Filters: US exchange, Cap $50M–$2B, non-excluded SIC, ADV ≥ $500K,
             US-domiciled (FMP country == US), Analyst ≤ 10.
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

    # ── Step 1: Collect all common-stock ticker symbols ────
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

    # ── Step 2: Sequential detail-fetch for market_cap + sic_code ───────────
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
        # Polygon locale == "us" means US domestic issuer (files 10-K/10-Q).
        # Foreign private issuers (file 20-F/6-K) return "global" or another value.
        # Exclude if locale is anything other than "us" — this is a harder gate
        # than the FMP country check below, which silently passes on null country.
        locale = detail.get("locale")
        if locale and locale != "us":
            logger.info("%s: Excluded — NON_US_LOCALE (locale=%s)", ticker, locale)
            continue
        sic = detail.get("sic_code")
        sector = _resolve_sector_with_fmp_backstop(ticker, sic, fmp_key)
        if sector is None:
            # Either SIC-excluded, FMP-excluded, or unknown to both sources.
            # Unknown → excluded is intentional: better to lose a few real names
            # than to silently classify REITs/financials as "Other" and pollute
            # downstream sector-relative scoring.
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
        # Foreign private issuer check — FMP /profile returns country; require US.
        # Whitelist approach: exclude if country is non-US OR missing. Missing country
        # was the original bug — it silently passed foreign issuers like ESEA and BWMX
        # that trade on US exchanges but file 20-F/6-K, breaking the SEC fetcher.
        # The Polygon locale check in Step 2 is the primary gate; this is defense-in-depth.
        profile = _fetch_fmp_profile(cand.ticker, fmp_key)
        country = profile.get("country")
        if not country or country.upper() != "US":
            logger.info("%s: Excluded — FOREIGN_ISSUER (country=%s)", cand.ticker, country or "unknown")
            return None

        count = _fetch_analyst_count(cand.ticker, fmp_key)
        cand.analyst_count = count
        time.sleep(0.5)  # Proactive pacing to protect FMP 300 req/min

        if count is not None and count > 10:
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

    logger.info("Final universe after foreign-issuer + analyst ≤ 10 filter: %d candidates", len(final))

    if final:
        _save_universe_cache(final)

    return final


def filter_by_profitability(universe: List[UniverseCandidate], raw_data_map: Dict[str, dict]) -> List[UniverseCandidate]:
    """
    Exclude tickers that fail data quality gates. Runs after data fetch but before scoring.

    Gates (in order):
      1. BROKEN_UNIT_ECONOMICS     — gm < _GM_FLOOR_BROKEN[sector] (sector-calibrated)
      3. PRE_REVENUE_BIOTECH       — gm > 0.95 (near-100% GM = placeholder before first revenue)
      4. INSUFFICIENT_QUALITY_DATA — gm is None AND rev_growth is None

    Gate 2 (NO_PROFITABILITY_PATH) removed — ROIC and FCF conversion in the quality
    factor now handle deteriorating economics continuously through scoring.
    """
    filtered: List[UniverseCandidate] = []
    exclusions = {
        "BROKEN_UNIT_ECONOMICS": 0,
        "PRE_REVENUE_BIOTECH": 0,
        "INSUFFICIENT_QUALITY_DATA": 0,
    }

    for cand in universe:
        ticker = cand.ticker
        data = raw_data_map.get(ticker, {})

        fmp_quality = data.get("fmp", {})
        fmp_inc = fmp_quality.get("income_statement", [])

        # Gross Margin check
        gm: Optional[float] = None
        if fmp_inc:
            rev = fmp_inc[0].get("revenue")
            gp  = fmp_inc[0].get("grossProfit")
            if rev and rev != 0 and gp is not None:
                gm = gp / rev

        # Revenue Growth check (used only for gate 4)
        rev_growth: Optional[float] = None
        if len(fmp_inc) >= 2:
            r1 = fmp_inc[0].get("revenue")
            r2 = fmp_inc[1].get("revenue")
            if r1 is not None and r2 and r2 != 0:
                rev_growth = (r1 - r2) / abs(r2)

        # 1. Broken unit economics — sector-calibrated GM floor
        gm_floor = _GM_FLOOR_BROKEN.get(cand.sector, _GM_FLOOR_BROKEN["Other"])
        if gm is not None and gm < gm_floor:
            exclusions["BROKEN_UNIT_ECONOMICS"] += 1
            logger.info("%s: Excluded — BROKEN_UNIT_ECONOMICS (gm=%.3f, floor=%.2f, sector=%s)", ticker, gm, gm_floor, cand.sector)
            continue

        # 3. Pre-revenue biotech signature — near-100% GM is a service placeholder
        if gm is not None and gm > 0.95:
            exclusions["PRE_REVENUE_BIOTECH"] += 1
            logger.info("%s: Excluded — PRE_REVENUE_BIOTECH (gm=%.3f)", ticker, gm)
            continue

        # 4. Insufficient data
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
    # Diagnostic: first 5 remaining tickers
    sample = [c.ticker for c in filtered[:5]]
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