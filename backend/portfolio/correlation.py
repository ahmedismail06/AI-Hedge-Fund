"""
Correlation Manager — Component 4 (Portfolio Construction & Sizing).

Checks whether a candidate stock is too correlated with existing open positions
using 60-day rolling Pearson correlation of daily closing prices.

Rules enforced:
  Rule 1 — Pair correlation: if ANY candidate↔position pair has 60-day
            correlation > 0.75, set correlation_flag=True and describe the
            most-correlated offending pair in correlation_note.

  Rule 2 — Sector concentration: if 3 or more open positions in the SAME
            sector as the candidate already exceed 25% combined gross exposure,
            set correlation_flag=True and record the sector concentration
            context in correlation_note.

Both rules are checked independently; if both fire the note combines both
findings. Only Rule 1 requires price data; Rule 2 is pure arithmetic.
"""

import logging
from typing import Optional

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Thresholds (domain rules)
_CORR_THRESHOLD: float = 0.75
_SECTOR_POSITION_COUNT_THRESHOLD: int = 3
_SECTOR_GROSS_EXPOSURE_THRESHOLD: float = 0.25  # 25 % of portfolio as a fraction


def _fetch_close_prices(tickers: list[str]) -> pd.DataFrame:
    """
    Fetch 3-month daily closing prices for a list of tickers via yfinance.

    Returns a DataFrame with ticker symbols as columns and dates as the index.
    Tickers that fail to download are silently dropped — the caller handles
    missing columns gracefully.

    The '3mo' period reliably covers at least 60 trading days and is the
    standard look-back window for this module.
    """
    if not tickers:
        return pd.DataFrame()

    try:
        raw = yf.download(
            tickers=tickers,
            period="3mo",
            interval="1d",
            auto_adjust=True,
            progress=False,
        )
    except Exception as exc:
        logger.warning("yfinance.download failed for %s: %s", tickers, exc)
        return pd.DataFrame()

    # yfinance returns multi-level columns when multiple tickers are requested.
    # Extract only the "Close" level; for a single ticker it returns a flat
    # DataFrame directly — handle both shapes.
    if raw.empty:
        return pd.DataFrame()

    if isinstance(raw.columns, pd.MultiIndex):
        close = raw["Close"] if "Close" in raw.columns.get_level_values(0) else pd.DataFrame()
    else:
        # Single ticker: columns are OHLCV field names
        if "Close" in raw.columns:
            close = raw[["Close"]].rename(columns={"Close": tickers[0]})
        else:
            close = pd.DataFrame()

    # Normalise column names to upper-case for consistent lookup
    if not close.empty:
        close.columns = [str(c).upper() for c in close.columns]

    return close


def _compute_pairwise_correlation(
    candidate: str,
    position_tickers: list[str],
    close_df: pd.DataFrame,
) -> list[tuple[str, float]]:
    """
    Compute Pearson correlation between the candidate and each position ticker
    using the provided close price DataFrame.

    Returns a list of (ticker, correlation_value) for every pair where both
    columns are present in close_df and contain enough data. Pairs where
    either column is missing or has fewer than 30 non-null overlapping
    observations are skipped with a warning.
    """
    candidate_upper = candidate.upper()
    results: list[tuple[str, float]] = []

    if candidate_upper not in close_df.columns:
        logger.warning(
            "Candidate %s not found in price data — skipping pair correlation check",
            candidate_upper,
        )
        return results

    for pos_ticker in position_tickers:
        pos_upper = pos_ticker.upper()
        if pos_upper not in close_df.columns:
            logger.warning(
                "Position ticker %s missing from price data — skipping pair (%s, %s)",
                pos_upper, candidate_upper, pos_upper,
            )
            continue

        pair = close_df[[candidate_upper, pos_upper]].dropna()
        if len(pair) < 30:
            logger.warning(
                "Insufficient overlapping price history (%d rows) for pair (%s, %s) — skipping",
                len(pair), candidate_upper, pos_upper,
            )
            continue

        try:
            corr_matrix = pair.corr()
            corr_value = corr_matrix.loc[candidate_upper, pos_upper]
            if pd.isna(corr_value):
                logger.warning(
                    "Correlation is NaN for pair (%s, %s) — skipping",
                    candidate_upper, pos_upper,
                )
                continue
            results.append((pos_upper, float(corr_value)))
        except Exception as exc:
            logger.warning(
                "Correlation computation failed for pair (%s, %s): %s",
                candidate_upper, pos_upper, exc,
            )

    return results


def check_correlation(
    candidate_ticker: str,
    candidate_sector: Optional[str],
    open_positions: list[dict],
    portfolio_value: float,
) -> tuple[bool, Optional[str]]:
    """
    Check whether adding the candidate stock violates correlation or sector
    concentration rules against the current open positions.

    Args:
        candidate_ticker: Ticker symbol of the stock being evaluated.
        candidate_sector: GICS sector of the candidate (e.g. 'Healthcare').
                          Pass None if unknown — Rule 2 will be skipped.
        open_positions:   List of open position dicts.  Each dict must contain:
                            'ticker'          (str)   — ticker symbol
                            'sector'          (str | None) — GICS sector
                            'pct_of_portfolio' (float) — weight as a fraction
                                              of portfolio NAV (0–1)
                            'direction'       (str)   — 'LONG' or 'SHORT'
                          Unknown or missing keys are handled gracefully.
        portfolio_value:  Current portfolio NAV in dollars.  Not used in
                          calculations directly but available for future
                          extensions (e.g. dollar-weighted concentration).

    Returns:
        (correlation_flag, correlation_note)
        correlation_flag  — True if any rule is breached; False otherwise.
        correlation_note  — Descriptive string when flag is True; None otherwise.

    Rule 1: ANY pair (candidate vs open position) has 60-day correlation > 0.75.
    Rule 2: 3+ open positions in the SAME sector exceed 25% combined gross exposure.

    If open_positions is empty the function returns (False, None) immediately.
    """
    if not open_positions:
        return False, None

    notes: list[str] = []
    flag = False

    # ── Rule 2: Sector concentration check (no price data needed) ─────────────
    if candidate_sector is not None:
        same_sector_positions = [
            p for p in open_positions
            if p.get("sector") == candidate_sector
        ]
        if len(same_sector_positions) >= _SECTOR_POSITION_COUNT_THRESHOLD:
            combined_exposure = sum(
                abs(p.get("pct_of_portfolio", 0.0)) for p in same_sector_positions
            )
            if combined_exposure > _SECTOR_GROSS_EXPOSURE_THRESHOLD:
                flag = True
                tickers_in_sector = [p.get("ticker", "?") for p in same_sector_positions]
                notes.append(
                    f"Sector concentration: {len(same_sector_positions)} open "
                    f"{candidate_sector} positions "
                    f"({', '.join(tickers_in_sector)}) already represent "
                    f"{combined_exposure * 100:.1f}% gross exposure "
                    f"(threshold: {_SECTOR_GROSS_EXPOSURE_THRESHOLD * 100:.0f}%); "
                    f"candidate capped at micro (1%)."
                )
                logger.info(
                    "Rule 2 triggered for %s: %d %s positions at %.1f%% gross",
                    candidate_ticker, len(same_sector_positions), candidate_sector,
                    combined_exposure * 100,
                )

    # ── Rule 1: Pair correlation check (requires price data) ──────────────────
    position_tickers = [p.get("ticker") for p in open_positions if p.get("ticker")]
    if not position_tickers:
        # No tickers available — skip price-based check
        if flag:
            return True, " | ".join(notes)
        return False, None

    all_tickers = [candidate_ticker.upper()] + [t.upper() for t in position_tickers]
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_tickers: list[str] = []
    for t in all_tickers:
        if t not in seen:
            seen.add(t)
            unique_tickers.append(t)

    close_df = _fetch_close_prices(unique_tickers)

    if close_df.empty:
        logger.warning(
            "No price data returned for %s and positions — skipping pair correlation check",
            candidate_ticker,
        )
        if flag:
            return True, " | ".join(notes)
        return False, None

    corr_pairs = _compute_pairwise_correlation(
        candidate=candidate_ticker,
        position_tickers=position_tickers,
        close_df=close_df,
    )

    # Find pairs that breach the threshold
    breaching: list[tuple[str, float]] = [
        (ticker, corr) for ticker, corr in corr_pairs if corr > _CORR_THRESHOLD
    ]

    if breaching:
        flag = True
        # Sort descending by correlation value so the most-correlated pair leads
        breaching_sorted = sorted(breaching, key=lambda x: x[1], reverse=True)
        pair_descriptions = [
            f"{t} ({corr:.2f})" for t, corr in breaching_sorted
        ]
        notes.append(
            f"High pair correlation with: {', '.join(pair_descriptions)} "
            f"(threshold: {_CORR_THRESHOLD}; 60-day daily closes)."
        )
        logger.info(
            "Rule 1 triggered for %s: correlated pairs: %s",
            candidate_ticker, breaching_sorted,
        )

    if flag:
        return True, " | ".join(notes)

    return False, None
