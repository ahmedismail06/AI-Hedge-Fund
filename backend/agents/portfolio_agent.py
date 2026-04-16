"""
Portfolio Agent — Component 4 (Portfolio Construction & Sizing).

Pure-quant 5-phase pipeline.  No LLM call is made; all sizing is deterministic
fractional Kelly unless explicitly overridden by the PM orchestrator.
The agent is triggered by research_scheduler.py after a memo is written for a ticker.

Pipeline:
  Phase 1 — Load memo from Supabase, validate verdict and conviction floor.
  Phase 2 — Read current macro regime from macro_briefings.
  Phase 3 — Load open positions, compute exposure state, fetch live entry price.
  Phase 4 — Kelly sizing (or PM override), correlation check, exposure-breach guard.
  Phase 5 — Build SizingRecommendation, upsert to positions table, return.

Entry point:
    async def run_portfolio_sizing(memo_id: str, portfolio_value: float, override_dollar_amount: float = None) -> SizingRecommendation
"""

import logging
import os
from datetime import datetime
from typing import Optional

import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

from backend.memory.vector_store import _get_client
from backend.portfolio import correlation, exposure_tracker, sizing_engine
from backend.portfolio.schemas import PortfolioSnapshot, SizingRecommendation
from backend.notifications.events import notify_event

logger = logging.getLogger(__name__)

# ── Module-level fallbacks ────────────────────────────────────────────────────

_VALID_REGIMES = {"Risk-On", "Risk-Off", "Transitional", "Stagflation"}


# ── Error class ───────────────────────────────────────────────────────────────


class PortfolioAgentError(Exception):
    """Raised when the portfolio agent cannot proceed with sizing."""


# ── Phase helpers ─────────────────────────────────────────────────────────────


def _load_memo(memo_id: str) -> dict:
    """
    Phase 1: Fetch memo row from Supabase by primary key.

    Returns the raw row dict with an extra '_memo_json_parsed' key containing
    the parsed memo_json dict.  Raises PortfolioAgentError if the memo is not
    found, the verdict is not LONG, or the conviction score is below 5.0.
    """
    try:
        client = _get_client()
        result = (
            client.table("memos")
            .select("*")
            .eq("id", memo_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        raise PortfolioAgentError(f"Phase 1: Supabase query failed — {exc}") from exc

    if not result.data:
        raise PortfolioAgentError(f"Phase 1: memo {memo_id!r} not found in memos table")

    row = result.data[0]

    # memo_json may be a dict already (JSONB) or a JSON string — normalise
    memo_json = row.get("memo_json") or {}
    if isinstance(memo_json, str):
        import json as _json
        try:
            memo_json = _json.loads(memo_json)
        except Exception:
            memo_json = {}
    row["_memo_json_parsed"] = memo_json

    verdict = memo_json.get("verdict") or row.get("verdict", "")
    conviction_score = float(memo_json.get("conviction_score") or row.get("conviction_score") or 0.0)

    if verdict != "LONG":
        raise PortfolioAgentError(
            f"Phase 1: SHORT verdicts deferred to Phase 2 (verdict={verdict!r})"
        )
    if conviction_score < 5.0:
        raise PortfolioAgentError(
            f"Phase 1: conviction too low to size (conviction_score={conviction_score:.1f})"
        )

    logger.info(
        "Phase 1 complete | memo_id=%s ticker=%s verdict=%s conviction=%.1f",
        memo_id,
        memo_json.get("ticker") or row.get("ticker", "?"),
        verdict,
        conviction_score,
    )
    return row


def _read_regime() -> str:
    """
    Phase 2: Read the most recent macro regime from Supabase macro_briefings.

    Returns "Risk-On" on any failure, logging a warning.
    """
    try:
        client = _get_client()
        result = (
            client.table("macro_briefings")
            .select("regime")
            .order("date", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            regime = result.data[0].get("regime")
            if regime in _VALID_REGIMES:
                logger.info("Phase 2: regime read from Supabase — %s", regime)
                return regime
            logger.warning(
                "Phase 2: unrecognised regime %r in macro_briefings — defaulting to Risk-On",
                regime,
            )
    except Exception as exc:
        logger.warning(
            "Phase 2: failed to read regime from Supabase (%s) — defaulting to Risk-On", exc
        )
    return "Risk-On"


def _load_open_positions() -> list:
    """
    Phase 3a: Fetch all rows from the positions table where status = 'OPEN'.

    Returns an empty list on any Supabase failure (non-blocking).
    """
    try:
        client = _get_client()
        result = (
            client.table("positions")
            .select("*")
            .eq("status", "OPEN")
            .execute()
        )
        positions = result.data or []
        logger.info("Phase 3: loaded %d open positions", len(positions))
        return positions
    except Exception as exc:
        logger.warning("Phase 3: could not load open positions (%s) — treating as empty", exc)
        return []


def _fetch_entry_price(ticker: str) -> float:
    """
    Phase 3b: Fetch the current market price for *ticker* via yfinance.

    Raises PortfolioAgentError if the price cannot be retrieved.
    """
    try:
        info = yf.Ticker(ticker).info
        price = info.get("regularMarketPrice") or info.get("currentPrice")
        if price and float(price) > 0:
            logger.info("Phase 3: entry price for %s = %.4f", ticker, float(price))
            return float(price)
    except Exception as exc:
        logger.warning("Phase 3: yfinance.Ticker(%s).info raised — %s", ticker, exc)

    raise PortfolioAgentError(f"Phase 3: could not fetch entry price for {ticker}")


def _build_portfolio_snapshot_after(
    exposure_state: dict,
    new_dollar_size: float,
    new_sector: Optional[str],
    portfolio_value: float,
    open_positions: list,
) -> PortfolioSnapshot:
    """
    Build the projected PortfolioSnapshot assuming the new LONG position is added.

    Adds the new position's notional to the gross and net exposure fractions,
    updates sector concentration, and increments position count by 1.
    """
    safe_value = portfolio_value if portfolio_value > 0 else 1.0
    new_pct = abs(new_dollar_size) / safe_value

    gross_after = round(float(exposure_state["gross_exposure_pct"]) + new_pct, 6)
    net_after = round(float(exposure_state["net_exposure_pct"]) + new_pct, 6)

    # Project sector concentration including the new position
    sector_conc: dict = dict(exposure_state.get("sector_concentration") or {})
    if new_sector:
        sector_conc[new_sector] = round(
            sector_conc.get(new_sector, 0.0) + new_pct, 6
        )

    return PortfolioSnapshot(
        gross_exposure_pct=gross_after,
        net_exposure_pct=net_after,
        sector_concentration=sector_conc,
        position_count=len(open_positions) + 1,
    )


def _upsert_position(rec: dict) -> None:
    """
    Phase 5: Insert a new PENDING_APPROVAL row into the positions table.

    Errors are logged but never re-raised — the SizingRecommendation is still
    returned to the caller even if the write fails.
    """
    try:
        client = _get_client()
        client.table("positions").insert(rec).execute()
        logger.info("Phase 5: position row inserted for ticker=%s", rec.get("ticker"))
    except Exception as exc:
        logger.error("Phase 5: positions insert failed — %s", exc)


# ── Main entry point ──────────────────────────────────────────────────────────


async def run_portfolio_sizing(
    memo_id: str,
    portfolio_value: Optional[float] = None,
    auto_approve: bool = False,
    override_dollar_amount: Optional[float] = None,
) -> SizingRecommendation:
    """
    Run the 5-phase portfolio sizing pipeline for a completed InvestmentMemo.

    Parameters
    ----------
    memo_id:
        UUID of the row in the Supabase `memos` table to size.
    portfolio_value:
        Current total portfolio NAV in USD.  If None or <= 0, reads the
        PORTFOLIO_VALUE env-var; defaults to $25,000.
    auto_approve:
        If True, write the position directly as APPROVED (PM has already
        decided EXECUTE).  If False, write as PENDING_APPROVAL (legacy path).
    override_dollar_amount:
        If provided, bypasses Kelly sizing and automated correlation reductions, 
        using this explicit size instead.

    Returns
    -------
    SizingRecommendation
        Fully populated sizing record.  The record has already been inserted
        into the `positions` table with status PENDING_APPROVAL.

    Raises
    ------
    PortfolioAgentError
        On hard stops: non-LONG verdict, conviction below 5.0, missing entry
        price, sizing engine ValueError, or exposure-limit breach.
    """
    if portfolio_value is None or portfolio_value <= 0:
        from backend.broker.ibkr import get_portfolio_value
        portfolio_value = get_portfolio_value()
        logger.info("portfolio_value resolved from broker/env: $%.2f", portfolio_value)

    today_str = datetime.now().strftime("%Y-%m-%d")

    # ── Phase 1: Load and validate memo ───────────────────────────────────────
    logger.info("=== Portfolio sizing starting | memo_id=%s ===", memo_id)
    row = _load_memo(memo_id)
    memo_json: dict = row["_memo_json_parsed"]

    ticker: str = str(memo_json.get("ticker") or row.get("ticker", "")).upper()
    conviction_score: float = float(
        memo_json.get("conviction_score") or row.get("conviction_score") or 0.0
    )
    sector: Optional[str] = memo_json.get("sector") or row.get("sector") or None
    target_price: Optional[float] = (
        float(memo_json["price_target"])
        if memo_json.get("price_target") is not None
        else None
    )
    direction = "LONG"  # Phase 1 is long-only; SHORT deferred to Phase 2

    # ── Phase 2: Read macro regime ─────────────────────────────────────────────
    regime = _read_regime()

    # ── Phase 3: Open positions, exposure state, entry price ──────────────────
    open_positions = _load_open_positions()
    exposure_state = exposure_tracker.get_current_exposure(
        open_positions, portfolio_value, regime
    )

    entry_price = _fetch_entry_price(ticker)

    # ── Phase 4: Size + constrain ──────────────────────────────────────────────

    # 4a — Kelly sizing (or PM Override handling)
    sizing = {}
    try:
        sizing = sizing_engine.calculate_size(
            conviction_score=conviction_score,
            portfolio_value=portfolio_value,
            entry_price=entry_price,
            regime=regime,
        )
    except ValueError as exc:
        if override_dollar_amount is None:
            raise PortfolioAgentError(f"Phase 4: sizing engine — {exc}") from exc
        else:
            # Sizing engine rejected the trade, but we have a PM override
            logger.info("Phase 4: Base Kelly sizing rejected trade (%s), proceeding with PM Override.", exc)
            sizing = {
                "stop_loss_price": round(entry_price * 0.9, 4),
                "sizing_rationale": f"Base Kelly logic bypassed due to engine exception: {exc}",
                "kelly_fraction": 0.0
            }

    # Apply Override or Base Sizing
    if override_dollar_amount is not None:
        dollar_size = override_dollar_amount
        share_count = float(int(dollar_size // entry_price))
        size_label = "PM_OVERRIDE"
        pct_of_portfolio = round(dollar_size / portfolio_value, 6)
        stop_loss_price = sizing.get("stop_loss_price", round(entry_price * 0.9, 4))
        kelly_fraction = sizing.get("kelly_fraction", 0.0)
        
        base_rationale = sizing.get("sizing_rationale", "")
        sizing_rationale = f"Size explicitly set by PM orchestrator override (${dollar_size:,.2f}). Base Logic context: {base_rationale}"
    else:
        dollar_size = sizing["dollar_size"]
        share_count = float(sizing["share_count"])
        size_label = sizing["size_label"]
        pct_of_portfolio = sizing["pct_of_portfolio"]
        stop_loss_price = sizing["stop_loss_price"]
        sizing_rationale = sizing["sizing_rationale"]
        kelly_fraction = sizing.get("kelly_fraction", 0.0)

    # 4b — Correlation check
    correlation_flag, correlation_note = correlation.check_correlation(
        candidate_ticker=ticker,
        candidate_sector=sector,
        open_positions=open_positions,
        portfolio_value=portfolio_value,
    )

    rule2_fired = (
        correlation_flag
        and correlation_note is not None
        and "Sector concentration" in correlation_note
    )

    # Only apply automated size reductions if PM did NOT explicitly set the size
    if override_dollar_amount is None:
        if correlation_flag and rule2_fired:
            # Downgrade to micro: recalculate with conviction_score=5.0 and cap pct at 1%
            logger.info(
                "Phase 4: correlation Rule 2 fired for %s — downgrading to micro", ticker
            )
            notify_event("CORRELATION_FLAG", {
                "ticker": ticker,
                "rule": "Rule 2 — Sector Concentration",
                "size_before": size_label,
                "size_after": "micro",
                "note": correlation_note or "3+ positions >25% gross in same sector",
            })
            try:
                micro_sizing = sizing_engine.calculate_size(
                    conviction_score=5.0,
                    portfolio_value=portfolio_value,
                    entry_price=entry_price,
                    regime=regime,
                )
            except ValueError as exc:
                raise PortfolioAgentError(
                    f"Phase 4: micro downgrade sizing failed — {exc}"
                ) from exc

            # Enforce micro ceiling: 1% of portfolio
            micro_pct = min(micro_sizing["pct_of_portfolio"], 0.01)
            dollar_size = round(micro_pct * portfolio_value, 2)
            share_count = float(int(dollar_size // entry_price))
            size_label = "micro"
            pct_of_portfolio = micro_pct
            stop_loss_price = micro_sizing["stop_loss_price"]
            sizing_rationale = (
                micro_sizing["sizing_rationale"]
                + " [Downgraded to micro: sector concentration rule triggered.]"
            )

            if share_count == 0:
                raise PortfolioAgentError(
                    f"Phase 4: micro downgrade produced 0 shares "
                    f"(dollar_size=${dollar_size:.2f}, entry_price=${entry_price:.2f})"
                )
        elif correlation_flag:
            # Rule 1 only: halve the position size (plan: correlation > 0.75 → 50% reduction)
            logger.info(
                "Phase 4: correlation Rule 1 fired for %s — reducing size by 50%%", ticker
            )
            notify_event("CORRELATION_FLAG", {
                "ticker": ticker,
                "rule": "Rule 1 — Pair Correlation > 0.75",
                "size_before": size_label,
                "size_after": f"{size_label} (50% reduced)",
                "note": correlation_note or "60-day Pearson correlation > 0.75 with existing position",
            })
            dollar_size = round(dollar_size * 0.5, 2)
            share_count = float(int(dollar_size // entry_price))
            pct_of_portfolio = round(dollar_size / portfolio_value, 6)
            sizing_rationale = (
                sizing_rationale + " [Reduced 50%: pair-correlation rule triggered.]"
            )
            if share_count == 0:
                raise PortfolioAgentError(
                    f"Phase 4: correlation reduction produced 0 shares "
                    f"(dollar_size=${dollar_size:.2f}, entry_price=${entry_price:.2f})"
                )
    elif correlation_flag:
        logger.info("Phase 4: correlation flag triggered, but bypassing automated size reduction due to PM override.")
        sizing_rationale += f" [Warning: {correlation_note} - Automated size reduction bypassed due to PM override]"

    # 4c — Exposure breach check (runs against the final dollar_size after any downgrade/override)
    breached, breach_reason = exposure_tracker.check_exposure_breach(
        new_dollar_size=dollar_size,
        new_direction=direction,
        new_sector=sector,
        current=exposure_state,
        portfolio_value=portfolio_value,
    )
    if breached:
        raise PortfolioAgentError(f"Phase 4: exposure limit breached — {breach_reason}")

    # ── Risk/reward ratio ──────────────────────────────────────────────────────
    risk_reward_ratio: Optional[float] = None
    if target_price is not None:
        upside = target_price - entry_price
        downside = entry_price - stop_loss_price
        if downside > 0:
            risk_reward_ratio = round(upside / downside, 4)

    # ── Phase 5: Build snapshot, recommendation, persist ──────────────────────
    portfolio_state_after = _build_portfolio_snapshot_after(
        exposure_state=exposure_state,
        new_dollar_size=dollar_size,
        new_sector=sector,
        portfolio_value=portfolio_value,
        open_positions=open_positions,
    )

    recommendation = SizingRecommendation(
        ticker=ticker,
        date=today_str,
        direction=direction,
        conviction_score=conviction_score,
        dollar_size=dollar_size,
        share_count=share_count,
        size_label=size_label,
        pct_of_portfolio=pct_of_portfolio,
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        target_price=target_price,
        risk_reward_ratio=risk_reward_ratio,
        sizing_rationale=sizing_rationale,
        correlation_flag=correlation_flag,
        correlation_note=correlation_note,
        sector=sector,
        regime_at_sizing=regime,
        portfolio_state_after=portfolio_state_after,
        status="APPROVED" if auto_approve else "PENDING_APPROVAL",
    )

    # ── Compute 3-tier stops from entry price + regime ────────────────────────
    # Tier 1: position stop  (-8% standard, -5% Risk-Off/Stagflation)
    # Tier 2: strategy stop  (-15% standard, -10% Risk-Off/Stagflation)
    # Tier 3: portfolio stop (-20% standard, -15% Risk-Off/Stagflation)
    _tight_regime = regime in ("Risk-Off", "Stagflation")
    stop_tier1 = round(entry_price * (1 - (0.05 if _tight_regime else 0.08)), 4)
    stop_tier2 = round(entry_price * (1 - (0.10 if _tight_regime else 0.15)), 4)
    stop_tier3 = round(entry_price * (1 - (0.15 if _tight_regime else 0.20)), 4)

    # ── next_earnings_date from memo context ──────────────────────────────────
    next_earnings_date: Optional[str] = None
    raw_earnings = memo_json.get("next_earnings_date")
    if raw_earnings:
        try:
            from datetime import date as _date
            # Accept ISO strings like "2026-05-15" or "2026-05-15T00:00:00"
            next_earnings_date = str(raw_earnings)[:10]
            _date.fromisoformat(next_earnings_date)  # validate
        except (ValueError, TypeError):
            next_earnings_date = None

    # Persist new position row to Supabase (errors are logged, not re-raised)
    _upsert_position({
        "ticker":                 ticker,
        "memo_id":                memo_id,
        "direction":              direction,
        "conviction_score":       conviction_score,
        "kelly_fraction":         kelly_fraction,
        "dollar_size":            dollar_size,
        "share_count":            share_count,
        "size_label":             size_label,
        "pct_of_portfolio":       pct_of_portfolio,
        "entry_price":            entry_price,
        "stop_loss_price":        stop_loss_price,
        "stop_tier1":             stop_tier1,
        "stop_tier2":             stop_tier2,
        "stop_tier3":             stop_tier3,
        "next_earnings_date":     next_earnings_date,
        "target_price":           target_price,
        "risk_reward_ratio":      risk_reward_ratio,
        "sizing_rationale":       sizing_rationale,
        "correlation_flag":       correlation_flag,
        "correlation_note":       correlation_note,
        "sector":                 sector,
        "regime_at_sizing":       regime,
        "portfolio_state_after":  portfolio_state_after.model_dump(),
        "status":                 "APPROVED" if auto_approve else "PENDING_APPROVAL",
    })

    logger.info(
        "=== Portfolio sizing complete | ticker=%s size_label=%s "
        "dollar_size=$%.2f pct=%.2f%% ===",
        ticker,
        size_label,
        dollar_size,
        pct_of_portfolio * 100,
    )
    notify_event("PORTFOLIO_SIZING_GENERATED", {
        "ticker": ticker,
        "size_label": size_label,
        "dollar_size": dollar_size,
        "pct_of_portfolio": pct_of_portfolio,
        "conviction_score": conviction_score,
        "stop_loss_price": stop_loss_price,
        "regime": regime,
    })

    return recommendation