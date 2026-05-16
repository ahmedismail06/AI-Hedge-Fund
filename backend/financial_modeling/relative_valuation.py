"""
Relative valuation module — EV/Revenue and EV/Gross Profit peer comps.

Used when the DCF applicability gate fires (high-growth, low/negative-FCF companies).
Peer multiples are precomputed nightly at 4PM and stored in the peer_multiples table;
this module only performs lookups at memo generation time (no per-peer API calls).
"""

import logging
import statistics
from typing import Optional

import yfinance as yf
from dotenv import load_dotenv

from backend.financial_modeling.schemas import RelativeValuationResult

load_dotenv()

logger = logging.getLogger(__name__)

# Market cap bucket boundaries in $M
_BUCKET_MICRO_MAX = 300.0
_BUCKET_SMALL_LOW_MAX = 1_000.0


def _market_cap_bucket(market_cap_m: float) -> str:
    if market_cap_m < _BUCKET_MICRO_MAX:
        return "micro"
    if market_cap_m < _BUCKET_SMALL_LOW_MAX:
        return "small_low"
    return "small_high"


def _get_ttm_revenue(polygon_financials_raw: dict) -> Optional[float]:
    """
    Sum revenues from the 4 most recent quarterly rows (fiscal_period != 'FY').
    Falls back to None if < 4 quarters found (caller uses revenue_recent instead).
    """
    try:
        results = polygon_financials_raw.get("results", [])
        quarterly = [
            r for r in results
            if r.get("fiscal_period") not in ("FY", None)
        ]
        quarterly.sort(key=lambda r: r.get("filing_date", ""), reverse=True)
        quarterly = quarterly[:4]
        if len(quarterly) < 4:
            return None
        total = 0.0
        for row in quarterly:
            rev = row["financials"]["income_statement"]["revenues"]["value"]
            if rev is None:
                return None
            total += rev
        return total
    except (KeyError, TypeError):
        return None


def _get_shares_outstanding(ticker: str, fmp_data: dict) -> Optional[float]:
    """
    Try Polygon FY balance_sheet → yfinance fallback. Same pattern as dcf.py.
    """
    poly_raw = fmp_data.get("polygon_financials_raw")
    if poly_raw:
        try:
            from backend.financial_modeling.dcf import _extract_fy_rows
            fy_rows = _extract_fy_rows(poly_raw)
            if fy_rows:
                val = fy_rows[0]["financials"]["balance_sheet"][
                    "common_shares_outstanding"
                ]["value"]
                if val is not None:
                    return float(val)
        except (KeyError, TypeError):
            pass
    try:
        info = yf.Ticker(ticker).info or {}
        val = info.get("sharesOutstanding")
        if val is not None:
            return float(val)
    except Exception as exc:
        logger.warning("_get_shares_outstanding(%s): yfinance failed — %s", ticker, exc)
    return None


def _get_supabase():
    from backend.db.utils import get_supabase_client
    return get_supabase_client()


def run_relative_valuation(
    ticker: str,
    fmp_data: dict,
) -> RelativeValuationResult:
    """
    Look up precomputed peer multiples from peer_multiples table and compute
    bull/base/bear price targets for the target ticker.

    Returns RelativeValuationResult with unavailable=True if:
    - peer_multiples row not found or peer_count < 5
    - Target ticker missing required inputs (market_cap, shares_outstanding)
    """
    try:
        # ── Sector + bucket ──────────────────────────────────────────────────
        sector = fmp_data.get("sector")
        if not sector:
            # Fall back to watchlist lookup
            try:
                client = _get_supabase()
                resp = (
                    client.table("watchlist")
                    .select("sector")
                    .eq("ticker", ticker.upper())
                    .order("run_date", desc=True)
                    .limit(1)
                    .execute()
                )
                if resp.data:
                    sector = resp.data[0].get("sector")
            except Exception:
                pass
        if not sector:
            return RelativeValuationResult(
                bull_target=0.0, base_target=0.0, bear_target=0.0,
                ev_revenue_used=0.0, peer_count=0,
                peer_median_ev_revenue=0.0, peer_25th_ev_revenue=0.0,
                peer_75th_ev_revenue=0.0, basis="",
                skipped_reason="missing_sector", unavailable=True,
            )

        market_cap = fmp_data.get("market_cap")
        if not market_cap or market_cap <= 0:
            return RelativeValuationResult(
                bull_target=0.0, base_target=0.0, bear_target=0.0,
                ev_revenue_used=0.0, peer_count=0,
                peer_median_ev_revenue=0.0, peer_25th_ev_revenue=0.0,
                peer_75th_ev_revenue=0.0, basis="",
                skipped_reason="missing_market_cap", unavailable=True,
            )

        bucket = _market_cap_bucket(market_cap / 1_000_000)

        # ── Peer multiples lookup ─────────────────────────────────────────────
        try:
            client = _get_supabase()
            resp = (
                client.table("peer_multiples")
                .select("*")
                .eq("sector", sector)
                .eq("market_cap_bucket", bucket)
                .limit(1)
                .execute()
            )
        except Exception as exc:
            logger.warning("run_relative_valuation(%s): DB lookup failed — %s", ticker, exc)
            return RelativeValuationResult(
                bull_target=0.0, base_target=0.0, bear_target=0.0,
                ev_revenue_used=0.0, peer_count=0,
                peer_median_ev_revenue=0.0, peer_25th_ev_revenue=0.0,
                peer_75th_ev_revenue=0.0, basis="",
                skipped_reason="db_error", unavailable=True,
            )

        if not resp.data:
            return RelativeValuationResult(
                bull_target=0.0, base_target=0.0, bear_target=0.0,
                ev_revenue_used=0.0, peer_count=0,
                peer_median_ev_revenue=0.0, peer_25th_ev_revenue=0.0,
                peer_75th_ev_revenue=0.0, basis="",
                skipped_reason="insufficient_peers", unavailable=True,
            )

        row = resp.data[0]
        peer_count = row.get("peer_count", 0)
        if peer_count < 5:
            return RelativeValuationResult(
                bull_target=0.0, base_target=0.0, bear_target=0.0,
                ev_revenue_used=0.0, peer_count=peer_count,
                peer_median_ev_revenue=0.0, peer_25th_ev_revenue=0.0,
                peer_75th_ev_revenue=0.0, basis="",
                skipped_reason="insufficient_peers", unavailable=True,
            )

        ev_rev_median = float(row["ev_revenue_median"])
        ev_rev_25th = float(row["ev_revenue_25th"])
        ev_rev_75th = float(row["ev_revenue_75th"])
        ev_gp_median = row.get("ev_gross_profit_median")
        if ev_gp_median is not None:
            ev_gp_median = float(ev_gp_median)

        # ── Target financials ─────────────────────────────────────────────────
        poly_raw = fmp_data.get("polygon_financials_raw") or {}
        ttm_rev = _get_ttm_revenue(poly_raw)
        if ttm_rev is None:
            ttm_rev = fmp_data.get("revenue_recent")
        if not ttm_rev or ttm_rev <= 0:
            return RelativeValuationResult(
                bull_target=0.0, base_target=0.0, bear_target=0.0,
                ev_revenue_used=ev_rev_median, peer_count=peer_count,
                peer_median_ev_revenue=ev_rev_median,
                peer_25th_ev_revenue=ev_rev_25th,
                peer_75th_ev_revenue=ev_rev_75th,
                basis="",
                skipped_reason="missing_revenue", unavailable=True,
            )

        gross_margin_pct = fmp_data.get("gross_margin_pct")
        ttm_gp: Optional[float] = None
        if gross_margin_pct is not None and gross_margin_pct > 0:
            ttm_gp = ttm_rev * (gross_margin_pct / 100.0)

        long_term_debt = fmp_data.get("long_term_debt") or 0.0
        cash = fmp_data.get("cash") or 0.0
        net_debt = long_term_debt - cash

        shares_outstanding = _get_shares_outstanding(ticker, fmp_data)
        if not shares_outstanding or shares_outstanding <= 0:
            return RelativeValuationResult(
                bull_target=0.0, base_target=0.0, bear_target=0.0,
                ev_revenue_used=ev_rev_median, peer_count=peer_count,
                peer_median_ev_revenue=ev_rev_median,
                peer_25th_ev_revenue=ev_rev_25th,
                peer_75th_ev_revenue=ev_rev_75th,
                basis="",
                skipped_reason="missing_shares", unavailable=True,
            )

        # ── Price target computation ──────────────────────────────────────────
        def _price_from_ev_rev(multiple: float) -> float:
            implied_ev = multiple * ttm_rev
            equity_value = max(0.0, implied_ev - net_debt)
            return max(0.01, equity_value / shares_outstanding)

        base_target = _price_from_ev_rev(ev_rev_median)
        bull_target = _price_from_ev_rev(ev_rev_75th)
        bear_target = _price_from_ev_rev(ev_rev_25th)

        # Secondary EV/Gross Profit check
        ev_gp_secondary: Optional[float] = None
        if ttm_gp and ttm_gp > 0 and ev_gp_median is not None:
            ev_gp_secondary = ev_gp_median

        basis = (
            f"Peer comps: median EV/Rev {ev_rev_median:.1f}x across {peer_count} "
            f"{sector} peers (mkt cap {bucket} bucket). "
        )
        if ev_gp_secondary is not None:
            basis += f"Secondary EV/GP: {ev_gp_secondary:.1f}x."

        return RelativeValuationResult(
            bull_target=round(bull_target, 2),
            base_target=round(base_target, 2),
            bear_target=round(bear_target, 2),
            primary_multiple="EV/Revenue",
            ev_revenue_used=round(ev_rev_median, 4),
            ev_gross_profit_secondary=round(ev_gp_secondary, 4) if ev_gp_secondary else None,
            peer_count=peer_count,
            peer_median_ev_revenue=round(ev_rev_median, 4),
            peer_25th_ev_revenue=round(ev_rev_25th, 4),
            peer_75th_ev_revenue=round(ev_rev_75th, 4),
            basis=basis,
            unavailable=False,
        )

    except Exception as exc:
        logger.error("run_relative_valuation(%s): unexpected failure — %s", ticker, exc)
        return RelativeValuationResult(
            bull_target=0.0, base_target=0.0, bear_target=0.0,
            ev_revenue_used=0.0, peer_count=0,
            peer_median_ev_revenue=0.0, peer_25th_ev_revenue=0.0,
            peer_75th_ev_revenue=0.0, basis="",
            skipped_reason=f"unexpected_error: {exc}", unavailable=True,
        )


# ── Peer multiples precomputation (called at 4PM after screener) ──────────────

def compute_peer_multiples_for_universe(active_tickers: list[dict]) -> None:
    """
    Compute and upsert EV/Revenue and EV/Gross Profit peer multiples for all
    (sector, market_cap_bucket) combos present in the active screener universe.

    active_tickers: list of dicts with keys {ticker, sector, market_cap_m}.
    Called by screener/scheduler.py after run_screening() completes.
    Never raises.
    """
    if not active_tickers:
        logger.warning("compute_peer_multiples_for_universe: empty ticker list")
        return

    try:
        from backend.fetchers.fmp_fetcher import fetch_fmp

        # ── Fetch financials for all peers ────────────────────────────────────
        ticker_data: dict[str, dict] = {}
        for entry in active_tickers:
            t = entry.get("ticker", "")
            if not t:
                continue
            try:
                data = fetch_fmp(t)
                ticker_data[t] = {
                    "market_cap": data.get("market_cap"),
                    "long_term_debt": data.get("long_term_debt") or 0.0,
                    "cash": data.get("cash") or 0.0,
                    "revenue_recent": data.get("revenue_recent"),
                    "gross_margin_pct": data.get("gross_margin_pct"),
                    "sector": entry.get("sector"),
                    "market_cap_m": entry.get("market_cap_m"),
                }
            except Exception as exc:
                logger.debug("compute_peer_multiples: fetch_fmp(%s) failed — %s", t, exc)

        # ── Group into (sector, bucket) and compute per-ticker multiples ──────
        # Structure: {(sector, bucket): [ev_rev, ...]}
        ev_rev_by_group: dict[tuple, list[float]] = {}
        ev_gp_by_group: dict[tuple, list[float]] = {}

        for t, d in ticker_data.items():
            mc = d.get("market_cap")
            debt = d.get("long_term_debt", 0.0)
            cash = d.get("cash", 0.0)
            rev = d.get("revenue_recent")
            gm_pct = d.get("gross_margin_pct")
            sector = d.get("sector")
            mc_m = d.get("market_cap_m")

            if not all([mc, rev, sector, mc_m]) or mc <= 0 or rev <= 0:
                continue

            ev = mc + debt - cash
            if ev <= 0:
                continue

            ev_rev = ev / rev
            bucket = _market_cap_bucket(mc_m)
            key = (sector, bucket)

            ev_rev_by_group.setdefault(key, []).append(ev_rev)

            if gm_pct is not None and gm_pct > 0:
                gp = rev * (gm_pct / 100.0)
                if gp > 0:
                    ev_gp = ev / gp
                    ev_gp_by_group.setdefault(key, []).append(ev_gp)

        # ── Widen band if < 5 peers: include adjacent bucket tickers ─────────
        # For groups with < 5 observations, merge with neighbouring bucket data
        all_keys = set(ev_rev_by_group.keys())
        for key in list(all_keys):
            if len(ev_rev_by_group.get(key, [])) < 5:
                sector, bucket = key
                # Collect all tickers in same sector regardless of bucket
                for other_key in all_keys:
                    if other_key[0] == sector and other_key != key:
                        ev_rev_by_group[key].extend(ev_rev_by_group.get(other_key, []))
                        ev_gp_by_group.setdefault(key, []).extend(
                            ev_gp_by_group.get(other_key, [])
                        )

        # ── Compute percentiles and upsert ────────────────────────────────────
        client = _get_supabase()
        rows_upserted = 0

        for key, ev_rev_vals in ev_rev_by_group.items():
            # Drop extreme outliers (> 100x EV/Revenue are data errors)
            ev_rev_vals = [v for v in ev_rev_vals if 0 < v < 100]
            if len(ev_rev_vals) < 5:
                logger.debug(
                    "compute_peer_multiples: skipping %s/%s — only %d valid peers",
                    key[0], key[1], len(ev_rev_vals),
                )
                continue

            sector, bucket = key
            quantiles_rev = statistics.quantiles(ev_rev_vals, n=4)  # Q1, Q2, Q3
            ev_rev_25 = quantiles_rev[0]
            ev_rev_50 = quantiles_rev[1]  # median
            ev_rev_75 = quantiles_rev[2]

            ev_gp_vals = [v for v in ev_gp_by_group.get(key, []) if 0 < v < 200]
            ev_gp_25 = ev_gp_50 = ev_gp_75 = None
            if len(ev_gp_vals) >= 5:
                quantiles_gp = statistics.quantiles(ev_gp_vals, n=4)
                ev_gp_25 = quantiles_gp[0]
                ev_gp_50 = quantiles_gp[1]
                ev_gp_75 = quantiles_gp[2]

            db_row = {
                "sector": sector,
                "market_cap_bucket": bucket,
                "ev_revenue_median": round(ev_rev_50, 4),
                "ev_revenue_25th": round(ev_rev_25, 4),
                "ev_revenue_75th": round(ev_rev_75, 4),
                "ev_gross_profit_median": round(ev_gp_50, 4) if ev_gp_50 else None,
                "ev_gross_profit_25th": round(ev_gp_25, 4) if ev_gp_25 else None,
                "ev_gross_profit_75th": round(ev_gp_75, 4) if ev_gp_75 else None,
                "peer_count": len(ev_rev_vals),
            }

            try:
                client.table("peer_multiples").upsert(
                    db_row, on_conflict="sector,market_cap_bucket"
                ).execute()
                rows_upserted += 1
                logger.info(
                    "peer_multiples upserted: %s / %s — %d peers, EV/Rev median %.2fx",
                    sector, bucket, len(ev_rev_vals), ev_rev_50,
                )
            except Exception as exc:
                logger.warning(
                    "compute_peer_multiples: upsert failed for %s/%s — %s",
                    sector, bucket, exc,
                )

        logger.info(
            "compute_peer_multiples_for_universe: done — %d groups upserted from %d tickers",
            rows_upserted, len(ticker_data),
        )

    except Exception as exc:
        logger.error("compute_peer_multiples_for_universe: unexpected failure — %s", exc)
