"""
Dedicated short universe builder.

Purpose: build a short-specific candidate pool *separate* from the long
watchlist. Longs hunt under-covered small caps; shorts hunt crowded,
deteriorating mid/large caps. Same universe ≠ right hunting ground.

Universe filters (institutional-grade defaults):
  • Market cap         $2B – $20B  (mid-cap sweet spot for shorts)
  • ADV                ≥ $5M       (avoid liquidity traps)
  • Exchange           NYSE | NASDAQ | AMEX (common stock only)
  • Sector exclusions  same as longs (Pharma, Mining, O&G, Financials, Utilities)
  • Squeeze filter     skip if short_interest > 20% of float
  • No-overlap         caller may pass exclude_tickers (e.g. long watchlist)

Per-ticker enrichment (trigger inputs):
  • Quarterly revenue × 4Q       (T1 revenue deceleration)
  • Quarterly gross margin × 4Q  (T2 GM compression YoY)
  • Quarterly weighted shares    (T7 dilution rate)
  • Cash, FCF TTM, runway months (T3 cash burn)
  • Debt / EBITDA TTM            (T5 leverage)
  • EV / Sales TTM               (T6 — sector-median comparison done in scorer)
  • Short interest pct           (squeeze diagnostic — already filtered)

Beneish is NOT computed here — that's the long screener's authoritative path,
and EXCLUDED tickers auto-enqueue to short_candidates via screening_agent.

On-disk JSON cache, 7-day TTL. Background rebuild on miss.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Universe filters ──────────────────────────────────────────────────────────
_MIN_CAP_M             = 2_000.0   # $2B
_MAX_CAP_M             = 20_000.0  # $20B
_MIN_ADV_K             = 5_000.0   # $5M average daily volume
_SI_SQUEEZE_THRESHOLD  = 20.0      # short_interest_pct above this = squeeze risk
_MIN_QUARTERS          = 2         # need ≥2 quarters of revenue to be useful

# ── Cache ─────────────────────────────────────────────────────────────────────
_CACHE_PATH = Path(__file__).resolve().parent.parent / ".short_universe_cache.json"
_CACHE_TTL_HOURS = 24 * 7  # weekly rebuild

# ── External APIs ─────────────────────────────────────────────────────────────
POLYGON_BASE = "https://api.polygon.io"
FMP_BASE     = "https://financialmodelingprep.com/stable"


@dataclass
class ShortUniverseRow:
    """Single ticker's trigger inputs. None fields → corresponding trigger evaluates False."""
    ticker: str
    market_cap_m: Optional[float] = None
    sector: Optional[str] = None
    adv_k: Optional[float] = None

    # Quarterly history (index 0 = most recent)
    revenue_q: list[Optional[float]] = field(default_factory=list)
    gross_margin_q: list[Optional[float]] = field(default_factory=list)  # ratio 0–1
    shares_diluted_q: list[Optional[float]] = field(default_factory=list)

    # TTM / point-in-time
    cash: Optional[float] = None
    fcf_ttm: Optional[float] = None
    cash_runway_months: Optional[float] = None

    # Multiples
    ev_to_sales_ttm: Optional[float] = None
    debt_to_ebitda_ttm: Optional[float] = None

    # Squeeze diagnostic (already filtered upstream, but kept for audit)
    short_interest_pct: Optional[float] = None


# ── Cache plumbing ────────────────────────────────────────────────────────────
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
            return None
        rows = data.get("rows") or data.get("tickers") or []
        logger.info("Short universe cache hit (%.1fh old, %d rows)", age_hours, len(rows))
        return rows
    except Exception as exc:
        logger.warning("Short universe cache read failed: %s", exc)
        return None


def _save_cache(rows: list[dict]) -> None:
    try:
        _CACHE_PATH.write_text(
            json.dumps({"cached_at": datetime.now(timezone.utc).isoformat(), "rows": rows})
        )
        logger.info("Short universe cache saved (%d rows)", len(rows))
    except Exception as exc:
        logger.warning("Short universe cache write failed: %s", exc)


def _rebuild_cache_background() -> None:
    try:
        rows = _fetch_universe_fresh()
        if rows:
            _save_cache(rows)
    except Exception as exc:
        logger.error("Short universe background rebuild failed: %s", exc)


# ── HTTP helpers ──────────────────────────────────────────────────────────────
def _fmp_get(url: str, max_retries: int = 3) -> Optional[requests.Response]:
    for attempt in range(max_retries):
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                return r
            if r.status_code == 429:
                time.sleep(6.0 * (attempt + 1))
                continue
            return None
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(2)
    return None


# ── Per-ticker enrichment ────────────────────────────────────────────────────
def _fetch_quarterly_history(ticker: str, fmp_key: str, limit: int = 4) -> tuple[
    list[Optional[float]], list[Optional[float]], list[Optional[float]]
]:
    """Return (revenue_q, gross_margin_q, shares_diluted_q) for last `limit` quarters, most-recent first."""
    url = f"{FMP_BASE}/income-statement?symbol={ticker}&period=quarter&limit={limit}&apikey={fmp_key}"
    r = _fmp_get(url)
    if r is None:
        return [], [], []
    try:
        rows = r.json()
        if not isinstance(rows, list):
            return [], [], []
    except Exception:
        return [], [], []

    rev: list[Optional[float]] = []
    gm: list[Optional[float]] = []
    shs: list[Optional[float]] = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        try:
            revenue = float(row["revenue"]) if row.get("revenue") is not None else None
        except (TypeError, ValueError):
            revenue = None
        try:
            gross_profit = float(row["grossProfit"]) if row.get("grossProfit") is not None else None
        except (TypeError, ValueError):
            gross_profit = None
        try:
            shares = (
                float(row["weightedAverageShsOutDil"])
                if row.get("weightedAverageShsOutDil") is not None
                else None
            )
        except (TypeError, ValueError):
            shares = None
        rev.append(revenue)
        gm.append(
            round(gross_profit / revenue, 4)
            if revenue and revenue > 0 and gross_profit is not None
            else None
        )
        shs.append(shares)
    return rev, gm, shs


def _fetch_key_metrics_ttm(ticker: str, fmp_key: str) -> dict:
    """Return subset of FMP key-metrics-ttm relevant to short triggers."""
    out: dict = {}
    r = _fmp_get(f"{FMP_BASE}/key-metrics-ttm?symbol={ticker}&limit=1&apikey={fmp_key}")
    if r is None:
        return out
    try:
        rows = r.json()
        if not isinstance(rows, list) or not rows:
            return out
        m = rows[0] if isinstance(rows[0], dict) else {}
    except Exception:
        return out
    for src_key, dst_key in [
        ("evToSalesTTM",       "ev_to_sales_ttm"),
        ("netDebtToEBITDATTM", "debt_to_ebitda_ttm"),
    ]:
        v = m.get(src_key)
        if v is None:
            continue
        try:
            out[dst_key] = float(v)
        except (TypeError, ValueError):
            pass
    return out


def _fetch_runway(ticker: str, fmp_key: str) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Best-effort (cash, fcf_ttm, runway_months). Sums up to 4 quarterly FCF when TTM missing."""
    cash: Optional[float] = None
    fcf_ttm: Optional[float] = None

    r = _fmp_get(f"{FMP_BASE}/balance-sheet-statement?symbol={ticker}&limit=1&apikey={fmp_key}")
    if r is not None:
        try:
            rows = r.json()
            if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                c = rows[0].get("cashAndCashEquivalents")
                if c is not None:
                    cash = float(c)
        except Exception:
            pass

    r = _fmp_get(f"{FMP_BASE}/cash-flow-statement?symbol={ticker}&period=quarter&limit=4&apikey={fmp_key}")
    if r is not None:
        try:
            rows = r.json()
            if isinstance(rows, list) and rows:
                ttm = 0.0
                ok = 0
                for row in rows[:4]:
                    if not isinstance(row, dict):
                        continue
                    fcf = row.get("freeCashFlow")
                    if fcf is not None:
                        try:
                            ttm += float(fcf)
                            ok += 1
                        except (TypeError, ValueError):
                            pass
                if ok > 0:
                    fcf_ttm = ttm * (4.0 / ok)
        except Exception:
            pass

    runway: Optional[float] = None
    if cash is not None and fcf_ttm is not None and fcf_ttm < 0:
        monthly_burn = abs(fcf_ttm) / 12.0
        if monthly_burn > 0:
            runway = round(cash / monthly_burn, 1)

    return cash, fcf_ttm, runway


def _fetch_short_interest_pct(ticker: str, polygon_key: str) -> Optional[float]:
    """Polygon short-interest endpoint. Returns short_interest as % of shares outstanding/float."""
    try:
        r = requests.get(
            f"{POLYGON_BASE}/stocks/v1/short-interest/{ticker}",
            params={"apiKey": polygon_key, "limit": 1},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        data = r.json()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    results = data.get("results")
    if not isinstance(results, list) or not results:
        return None
    row = results[0] if isinstance(results[0], dict) else {}
    si = row.get("short_interest")
    shares_out = row.get("shares_outstanding") or row.get("float")
    if not si or not shares_out:
        return None
    try:
        return round(float(si) / float(shares_out) * 100.0, 2)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


# ── Universe build ────────────────────────────────────────────────────────────
def _collect_polygon_tickers(polygon_key: str) -> list[str]:
    """Paginated pull of all common-stock tickers from major US exchanges."""
    target_exchanges = {"XNYS", "XNAS", "XASE"}
    out: list[str] = []
    next_url: Optional[str] = (
        f"{POLYGON_BASE}/v3/reference/tickers"
        f"?market=stocks&type=CS&active=true&limit=1000&apiKey={polygon_key}"
    )
    pages = 0
    while next_url and pages < 15:
        try:
            r = requests.get(next_url, timeout=20)
            pages += 1
        except Exception as exc:
            logger.warning("Short universe ticker list page %d failed: %s", pages, exc)
            break
        if r.status_code == 429:
            time.sleep(10)
            continue
        if r.status_code != 200:
            break
        time.sleep(0.4)
        data = r.json()
        for t in data.get("results", []):
            sym = t.get("ticker", "")
            if t.get("primary_exchange") in target_exchanges and sym:
                out.append(sym)
        np_url = data.get("next_url")
        next_url = f"{np_url}{'&' if '?' in np_url else '?'}apiKey={polygon_key}" if np_url else None
    return out


def _fetch_universe_fresh() -> list[dict]:
    """
    Full rebuild. Expensive — ~5–10 min depending on universe size. Cached weekly.
    """
    polygon_key = os.getenv("POLYGON_API_KEY")
    fmp_key = os.getenv("FMP_API_KEY")
    if not polygon_key or not fmp_key:
        logger.error("Short universe rebuild: missing POLYGON_API_KEY or FMP_API_KEY")
        return []

    from backend.screener.universe import (
        _fetch_ticker_detail,
        _fetch_adv_k,
        _resolve_sector_with_fmp_backstop,
    )

    all_symbols = _collect_polygon_tickers(polygon_key)
    logger.info("Short universe: %d raw tickers from Polygon", len(all_symbols))

    # ── Step 1: market cap + sector filter (with FMP backstop) ────────────────
    band: list[dict] = []
    for sym in all_symbols:
        detail = _fetch_ticker_detail(sym, polygon_key)
        time.sleep(0.2)
        if not detail:
            continue
        mc = detail.get("market_cap")
        if mc is None:
            continue
        cap_m = mc / 1_000_000
        if not (_MIN_CAP_M <= cap_m <= _MAX_CAP_M):
            continue
        sic = detail.get("sic_code")
        sector = _resolve_sector_with_fmp_backstop(sym, sic, fmp_key)
        if sector is None:
            # SIC-excluded, FMP-excluded, or unknown to both → drop.
            continue
        band.append({"ticker": sym, "market_cap_m": round(cap_m, 2), "sector": sector})
    logger.info("Short universe: %d after cap/sector filter", len(band))

    # ── Step 2: ADV filter ────────────────────────────────────────────────────
    adv_filtered: list[dict] = []
    for c in band:
        adv = _fetch_adv_k(c["ticker"], polygon_key)
        time.sleep(0.1)
        if adv is not None and adv >= _MIN_ADV_K:
            c["adv_k"] = round(adv, 2)
            adv_filtered.append(c)
    logger.info("Short universe: %d after ADV filter", len(adv_filtered))

    # ── Step 3: per-ticker trigger-input enrichment ──────────────────────────
    enriched: list[dict] = []
    for c in adv_filtered:
        sym = c["ticker"]

        # Squeeze filter — skip early to save FMP calls
        si_pct = _fetch_short_interest_pct(sym, polygon_key)
        if si_pct is not None and si_pct > _SI_SQUEEZE_THRESHOLD:
            logger.debug("Short universe: %s skipped (SI=%.1f%% > %.0f%% squeeze)",
                         sym, si_pct, _SI_SQUEEZE_THRESHOLD)
            continue

        revenue_q, gm_q, shares_q = _fetch_quarterly_history(sym, fmp_key, limit=4)
        time.sleep(0.15)
        if len([x for x in revenue_q if x is not None]) < _MIN_QUARTERS:
            continue

        km = _fetch_key_metrics_ttm(sym, fmp_key)
        time.sleep(0.15)

        cash, fcf_ttm, runway = _fetch_runway(sym, fmp_key)
        time.sleep(0.15)

        row = ShortUniverseRow(
            ticker=sym,
            market_cap_m=c["market_cap_m"],
            sector=c["sector"],
            adv_k=c.get("adv_k"),
            revenue_q=revenue_q,
            gross_margin_q=gm_q,
            shares_diluted_q=shares_q,
            cash=cash,
            fcf_ttm=fcf_ttm,
            cash_runway_months=runway,
            ev_to_sales_ttm=km.get("ev_to_sales_ttm"),
            debt_to_ebitda_ttm=km.get("debt_to_ebitda_ttm"),
            short_interest_pct=si_pct,
        )
        enriched.append(asdict(row))

    logger.info("Short universe: %d enriched rows ready for trigger scorer", len(enriched))
    return enriched


# ── Public API ────────────────────────────────────────────────────────────────
def build_short_universe(
    use_cache: bool = True,
    exclude_tickers: Optional[set[str]] = None,
) -> list[dict]:
    """
    Return the dedicated short universe — list of ShortUniverseRow dicts.

    Args:
        use_cache: When True (default), serve from cache if fresh. On miss spawn
                   background rebuild and return [] for today's run.
        exclude_tickers: Optional set of tickers to drop (e.g. today's long
                         watchlist — we never want overlap).

    Cache TTL: 7 days. Universe composition is stable enough for weekly rebuild.
    """
    if not use_cache:
        rows = _fetch_universe_fresh()
        if rows:
            _save_cache(rows)
    else:
        rows = _load_cache()
        if rows is None:
            logger.info("Short universe cache miss — spawning background rebuild; today returns []")
            threading.Thread(target=_rebuild_cache_background, daemon=True).start()
            return []

    if exclude_tickers:
        before = len(rows)
        rows = [r for r in rows if r["ticker"] not in exclude_tickers]
        logger.debug("Short universe: dropped %d tickers overlapping with exclude set", before - len(rows))

    return rows
