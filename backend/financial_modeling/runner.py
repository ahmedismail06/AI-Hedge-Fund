"""
Financial model runner — orchestrates DCF + earnings quality, persists to DB.
Called by research_agent.py Phase 2.5.
"""

import logging
import os
from datetime import date
from typing import Optional

from dotenv import load_dotenv
from supabase import create_client

from backend.financial_modeling.dcf import run_dcf, _unavailable_result, _dcf_is_applicable
from backend.financial_modeling.earnings_quality import run_earnings_quality
from backend.financial_modeling.relative_valuation import run_relative_valuation
from backend.financial_modeling.schemas import (
    DCFResult,
    EarningsQualityResult,
    FinancialModelOutput,
    RelativeValuationResult,
)

load_dotenv()

logger = logging.getLogger(__name__)

from backend.db.utils import get_supabase_client


def _get_client():
    """Return a fresh Supabase client."""
    return get_supabase_client()


def _read_risk_free_rate() -> float:
    """
    Read the current 10-year Treasury yield from the latest macro_briefings row.

    Looks for 'yield_10y' in the indicator_scores JSONB column.
    Returns a decimal rate (e.g. 0.045 for 4.5%). Defaults to 0.045 on any failure.

    Returns:
        Risk-free rate as decimal float.
    """
    try:
        client = _get_client()
        result = (
            client.table("macro_briefings")
            .select("indicator_scores")
            .order("date", desc=True)
            .limit(1)
            .execute()
        )
        if not result.data:
            return 0.045

        indicator_scores = result.data[0].get("indicator_scores")
        if not indicator_scores:
            return 0.045

        # indicator_scores may be a list of dicts or a plain dict
        if isinstance(indicator_scores, list):
            for item in indicator_scores:
                if isinstance(item, dict) and item.get("name") == "yield_10y":
                    val = item.get("value")
                    if val is not None:
                        rate = float(val)
                        return rate / 100.0 if rate > 1.0 else rate
        elif isinstance(indicator_scores, dict):
            val = indicator_scores.get("yield_10y")
            if val is not None:
                rate = float(val)
                return rate / 100.0 if rate > 1.0 else rate

        return 0.045

    except Exception as exc:
        logger.warning("_read_risk_free_rate: failed to read from Supabase — %s", exc)
        return 0.045


def _get_macro_regime() -> str:
    """
    Read the latest macro regime from macro_briefings.

    Returns:
        Regime string (e.g. 'Risk-On', 'Transitional'). Defaults to 'Transitional'.
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
            return result.data[0].get("regime", "Transitional")
        return "Transitional"

    except Exception as exc:
        logger.warning("_get_macro_regime: failed to read from Supabase — %s", exc)
        return "Transitional"


def _persist_model(
    output: FinancialModelOutput,
    dcf_skipped_reason: Optional[str] = None,
) -> None:
    """
    Upsert the financial model output to the financial_models table.

    Uses on_conflict='ticker,run_date' for idempotent re-runs.
    Logs a warning on failure but never raises.

    Args:
        output: Completed FinancialModelOutput to persist.
        dcf_skipped_reason: Reason string if DCF gate fired; None when DCF ran.
    """
    try:
        client = _get_client()

        dcf = output.dcf
        eq = output.earnings_quality
        rel = output.relative_valuation

        row = {
            "ticker": output.ticker.upper(),
            "run_date": output.run_date,
            "dcf_bull_target": dcf.bull.price_target if not dcf.unavailable else None,
            "dcf_base_target": dcf.base.price_target if not dcf.unavailable else None,
            "dcf_bear_target": dcf.bear.price_target if not dcf.unavailable else None,
            "wacc": dcf.wacc if not dcf.unavailable else None,
            "terminal_growth": dcf.terminal_growth if not dcf.unavailable else None,
            "beneish_m_score": eq.m_score,
            "beneish_gate": eq.beneish_gate,
            "accruals_ratio": eq.accruals_ratio,
            "quality_grade": eq.quality_grade,
            "model_json": output.model_json,
            "dcf_skipped_reason": dcf_skipped_reason,
            "valuation_method": output.valuation_method,
            "relative_bull_target": rel.bull_target if rel and not rel.unavailable else None,
            "relative_base_target": rel.base_target if rel and not rel.unavailable else None,
            "relative_bear_target": rel.bear_target if rel and not rel.unavailable else None,
            "peer_count": rel.peer_count if rel and not rel.unavailable else None,
            "ev_revenue_used": rel.ev_revenue_used if rel and not rel.unavailable else None,
        }

        client.table("financial_models").upsert(
            row, on_conflict="ticker,run_date"
        ).execute()

    except Exception as exc:
        logger.warning("_persist_model(%s): failed to upsert — %s", output.ticker, exc)


def _format_financial_modeling_context(output: FinancialModelOutput) -> str:
    """
    Format financial model output as a concise string for injection into research context.

    Args:
        output: Completed FinancialModelOutput.

    Returns:
        Multi-line formatted string summarising the selected valuation method and earnings quality.
    """
    dcf = output.dcf
    eq = output.earnings_quality
    rel = output.relative_valuation
    accruals_str = f"{eq.accruals_ratio:.2f}" if eq.accruals_ratio is not None else "N/A"

    # DCF ran successfully
    if not dcf.unavailable:
        key_drivers_str = " | ".join(dcf.key_drivers)
        lines = [
            "=== FINANCIAL MODEL (DCF) ===",
            (
                f"WACC: {dcf.wacc * 100:.1f}%  |  "
                f"Terminal growth: {dcf.terminal_growth * 100:.1f}%  |  "
                f"Quality grade: {eq.quality_grade}"
            ),
            (
                f"Bull target: ${dcf.bull.price_target:.2f}  |  "
                f"Base target: ${dcf.base.price_target:.2f}  |  "
                f"Bear target: ${dcf.bear.price_target:.2f}"
            ),
            f"Beneish gate: {eq.beneish_gate}  |  Accruals ratio: {accruals_str}",
            f"Key drivers: {key_drivers_str}",
        ]
        return "\n".join(lines)

    # DCF gated — relative valuation available
    if rel and not rel.unavailable:
        gp_str = (
            f"Secondary EV/Gross Profit: {rel.ev_gross_profit_secondary:.1f}x  |  "
            if rel.ev_gross_profit_secondary
            else ""
        )
        lines = [
            "=== FINANCIAL MODEL (Relative Valuation) ===",
            (
                f"Method: EV/Revenue comps  |  "
                f"Peers: {rel.peer_count}  |  "
                f"Quality grade: {eq.quality_grade}"
            ),
            (
                f"Bull target: ${rel.bull_target:.2f}  |  "
                f"Base target: ${rel.base_target:.2f}  |  "
                f"Bear target: ${rel.bear_target:.2f}"
            ),
            f"EV/Revenue applied: {rel.ev_revenue_used:.1f}x (peer median)  |  {gp_str}",
            f"Beneish gate: {eq.beneish_gate}  |  Accruals ratio: {accruals_str}",
            f"Basis: {rel.basis}",
        ]
        return "\n".join(lines)

    # Both unavailable
    dcf_reason = dcf.unavailable_reason or "unknown"
    rel_reason = (rel.skipped_reason if rel else None) or "not attempted"
    return (
        f"=== FINANCIAL MODEL ===\n"
        f"DCF unavailable — {dcf_reason}\n"
        f"Relative valuation unavailable — {rel_reason}\n"
        f"Earnings quality: {eq.quality_grade}"
    )


def run_financial_model(
    ticker: str,
    fmp_data: dict,
    polygon_financials: Optional[dict] = None,
) -> FinancialModelOutput:
    """
    Orchestrate DCF + earnings quality for a ticker and persist results.

    Reads macro regime and risk-free rate from Supabase to parameterise the DCF.
    Never raises — returns output with unavailable=True components on failure.

    Args:
        ticker: Stock ticker symbol.
        fmp_data: Dict from fetch_fmp() (extended with polygon_financials_raw, beta, etc.).
        polygon_financials: Optional explicit Polygon financials dict. Falls back to
                            fmp_data['polygon_financials_raw'] if not provided.

    Returns:
        FinancialModelOutput with DCF result, earnings quality result, and summary.
    """
    run_date = str(date.today())

    # Step 1: Resolve polygon financials source
    poly = polygon_financials or fmp_data.get("polygon_financials_raw")

    # Step 2: Macro regime
    regime = _get_macro_regime()

    # Step 3: Risk-free rate
    risk_free_rate = _read_risk_free_rate()

    # Step 4: Inject risk-free rate into a copy of fmp_data
    fmp_data_copy = {**fmp_data, "_risk_free_rate": risk_free_rate}

    # Step 5: DCF applicability gate → run DCF or relative valuation
    dcf_skipped_reason: Optional[str] = None
    rel_result: Optional[RelativeValuationResult] = None
    valuation_method = "none"

    try:
        dcf_applicable, skip_reason = _dcf_is_applicable(fmp_data_copy)
    except Exception as exc:
        logger.warning("run_financial_model(%s): _dcf_is_applicable raised — %s", ticker, exc)
        dcf_applicable, skip_reason = True, ""

    if dcf_applicable:
        try:
            dcf_result = run_dcf(ticker, fmp_data_copy, macro_regime=regime)
        except Exception as exc:
            logger.error("run_financial_model(%s): run_dcf raised unexpectedly — %s", ticker, exc)
            dcf_result = _unavailable_result(f"dcf_exception: {exc}")
        valuation_method = "dcf" if not dcf_result.unavailable else "none"
    else:
        logger.info(
            "run_financial_model(%s): DCF gated — %s, trying relative valuation",
            ticker, skip_reason,
        )
        dcf_skipped_reason = skip_reason
        dcf_result = _unavailable_result(f"dcf_not_applicable: {skip_reason}")
        try:
            rel_result = run_relative_valuation(ticker, fmp_data_copy)
        except Exception as exc:
            logger.error(
                "run_financial_model(%s): run_relative_valuation raised unexpectedly — %s",
                ticker, exc,
            )
            rel_result = None
        if rel_result and not rel_result.unavailable:
            valuation_method = "relative"

    # Step 6: Run earnings quality
    try:
        eq_result = run_earnings_quality(
            ticker,
            poly or {},
            fmp_data_copy,
            sector=fmp_data_copy.get("sector"),
        )
    except Exception as exc:
        logger.error(
            "run_financial_model(%s): run_earnings_quality raised unexpectedly — %s",
            ticker,
            exc,
        )
        eq_result = EarningsQualityResult(
            beneish_gate="INSUFFICIENT_DATA",
            unavailable=True,
        )

    # Step 7: Build partial dict for model_json (avoids circular reference)
    partial: dict = {
        "ticker": ticker.upper(),
        "run_date": run_date,
        "valuation_method": valuation_method,
        "dcf": dcf_result.model_dump(),
        "relative_valuation": rel_result.model_dump() if rel_result else None,
        "earnings_quality": eq_result.model_dump(),
    }

    # Step 8: Construct output — summary computed before final object creation
    # Build a temporary output to generate the summary string
    temp_output = FinancialModelOutput(
        ticker=ticker.upper(),
        run_date=run_date,
        dcf=dcf_result,
        earnings_quality=eq_result,
        relative_valuation=rel_result,
        valuation_method=valuation_method,
        summary="",
        model_json=partial,
    )
    summary = _format_financial_modeling_context(temp_output)

    output = FinancialModelOutput(
        ticker=ticker.upper(),
        run_date=run_date,
        dcf=dcf_result,
        earnings_quality=eq_result,
        relative_valuation=rel_result,
        valuation_method=valuation_method,
        summary=summary,
        model_json={**partial, "summary": summary},
    )

    # Step 9: Persist to Supabase
    _persist_model(output, dcf_skipped_reason=dcf_skipped_reason)

    # Step 10: Return
    return output
