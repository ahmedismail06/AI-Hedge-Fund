"""
Backfill valuation blocks for active memos.

Scope:
  - All tickers with status = 'OPEN' in positions table
  - All tickers with queued_for_research = true in the latest watchlist run

For each in-scope ticker:
  1. Load the single most-recent memo row (by created_at DESC)
  2. Re-run Component 10 only (run_financial_model) — no Claude, no research pipeline
  3. Patch memo_json with: valuation_method, dcf, relative_valuation, price_target,
     price_target_basis
  4. Set valuation_backfilled_at = now()
  5. Log before/after

Safety: never touches memos for tickers whose position is CLOSED.
Run from repo root: python scripts/backfill_valuations.py
"""

import json
import sys
import os
from datetime import datetime, timezone
from typing import Optional

# ── ensure repo root on sys.path ─────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from backend.db.utils import get_supabase_client
from backend.fetchers.fmp_fetcher import fetch_fmp
from backend.financial_modeling.runner import run_financial_model

# ── closed-position guard (immutable audit records — never touch) ─────────────
def _get_closed_tickers(client) -> set[str]:
    resp = client.table("positions").select("ticker,status").execute()
    return {r["ticker"].upper() for r in (resp.data or []) if r.get("status") == "CLOSED"}


def _get_open_tickers(client) -> list[str]:
    resp = (
        client.table("positions")
        .select("ticker")
        .eq("status", "OPEN")
        .execute()
    )
    return [r["ticker"].upper() for r in (resp.data or [])]


def _get_active_watchlist_tickers(client) -> list[str]:
    date_resp = (
        client.table("watchlist")
        .select("run_date")
        .order("run_date", desc=True)
        .limit(1)
        .execute()
    )
    if not date_resp.data:
        return []
    latest_date = date_resp.data[0]["run_date"]
    resp = (
        client.table("watchlist")
        .select("ticker")
        .eq("run_date", latest_date)
        .eq("queued_for_research", True)
        .execute()
    )
    return [r["ticker"].upper() for r in (resp.data or [])]


def _get_latest_memo(client, ticker: str) -> Optional[dict]:
    resp = (
        client.table("memos")
        .select("id,ticker,date,status,memo_json,created_at,valuation_backfilled_at")
        .eq("ticker", ticker.upper())
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if resp.data:
        return resp.data[0]
    return None


def _build_valuation_patch(output) -> dict:
    """
    Build the dict of fields to merge into memo_json from a FinancialModelOutput.
    Returns only the valuation-related keys — never overwrites thesis/risk/etc.
    """
    patch: dict = {"valuation_method": output.valuation_method}

    dcf = output.dcf
    rel = output.relative_valuation

    if not dcf.unavailable:
        patch["dcf"] = {
            "bull_target": dcf.bull.price_target,
            "base_target": dcf.base.price_target,
            "bear_target": dcf.bear.price_target,
            "wacc": dcf.wacc,
            "terminal_growth": dcf.terminal_growth,
            "key_drivers": dcf.key_drivers,
        }
        patch["price_target"] = dcf.base.price_target
        patch["price_target_basis"] = (
            f"DCF (WACC {dcf.wacc * 100:.1f}%, terminal {dcf.terminal_growth * 100:.1f}%)"
        )
    else:
        patch["dcf"] = None

    if rel and not rel.unavailable:
        patch["relative_valuation"] = {
            "bull_target": rel.bull_target,
            "base_target": rel.base_target,
            "bear_target": rel.bear_target,
            "primary_multiple": rel.primary_multiple,
            "ev_revenue_used": rel.ev_revenue_used,
            "peer_count": rel.peer_count,
            "basis": rel.basis,
        }
        if not patch.get("price_target"):
            patch["price_target"] = rel.base_target
            patch["price_target_basis"] = (
                f"Peer comps EV/Rev {rel.ev_revenue_used:.1f}x ({rel.peer_count} peers)"
            )
    else:
        patch["relative_valuation"] = None

    # When neither method produced a target, explicitly null out any stale price_target
    # that may have been written by a prior run. Leaving a stale DCF-era price_target in
    # memo_json while valuation_method=none causes the PM agent to receive a contradictory
    # signal: a firm price target with no supporting model.
    if "price_target" not in patch:
        patch["price_target"] = None
        patch["price_target_basis"] = None

    return patch


def main():
    client = get_supabase_client()

    # ── 1. Determine scope ────────────────────────────────────────────────────
    closed_tickers = _get_closed_tickers(client)
    open_tickers = _get_open_tickers(client)
    watchlist_tickers = _get_active_watchlist_tickers(client)

    in_scope = sorted(set(open_tickers) | set(watchlist_tickers))
    # Safety: remove any closed-position ticker that somehow made it in
    in_scope = [t for t in in_scope if t not in closed_tickers]

    print("=" * 70)
    print(f"VALUATION BACKFILL — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 70)
    print(f"\nScope:  {len(in_scope)} tickers")
    print(f"  Open positions:     {sorted(open_tickers)}")
    print(f"  Active watchlist:   {sorted(watchlist_tickers)}")
    print(f"  Closed (excluded):  {sorted(closed_tickers)}")
    print()

    results = []
    now_ts = datetime.now(timezone.utc).isoformat()

    for ticker in in_scope:
        print(f"── {ticker} {'─' * (50 - len(ticker))}")

        # ── 2. Load most recent memo ─────────────────────────────────────────
        memo = _get_latest_memo(client, ticker)
        if not memo:
            print(f"  SKIP — no memo found\n")
            results.append({
                "ticker": ticker,
                "outcome": "SKIP_NO_MEMO",
            })
            continue

        memo_id = memo["id"]
        memo_json = memo["memo_json"] or {}
        old_price_target = memo_json.get("price_target")
        old_price_target_basis = memo_json.get("price_target_basis")
        old_valuation_method = memo_json.get("valuation_method")

        print(f"  Memo ID:     {memo_id}")
        print(f"  Memo date:   {memo['date']}  status={memo['status']}")
        print(f"  Old targets: price_target={old_price_target}  basis={old_price_target_basis!r}")

        # ── 3. Fetch market data + run Component 10 ──────────────────────────
        try:
            fmp_data = fetch_fmp(ticker)
        except Exception as exc:
            print(f"  ERROR fetch_fmp: {exc}\n")
            results.append({
                "ticker": ticker,
                "memo_id": memo_id,
                "outcome": f"ERROR_FETCH: {exc}",
            })
            continue

        try:
            output = run_financial_model(ticker, fmp_data)
        except Exception as exc:
            print(f"  ERROR run_financial_model: {exc}\n")
            results.append({
                "ticker": ticker,
                "memo_id": memo_id,
                "outcome": f"ERROR_FM: {exc}",
            })
            continue

        # ── 4. Build patch and merge into memo_json ──────────────────────────
        patch = _build_valuation_patch(output)
        new_memo_json = {**memo_json, **patch}

        new_price_target = patch.get("price_target")
        new_price_target_basis = patch.get("price_target_basis")
        new_vm = output.valuation_method

        print(f"  Gate result: valuation_method={new_vm}")
        if output.dcf and not output.dcf.unavailable:
            print(
                f"  DCF targets: bull={output.dcf.bull.price_target}  "
                f"base={output.dcf.base.price_target}  "
                f"bear={output.dcf.bear.price_target}"
            )
            print(f"  DCF spread:  ±{abs(output.dcf.bull.price_target - output.dcf.base.price_target):.2f} "
                  f"(bull gap) / ±{abs(output.dcf.base.price_target - output.dcf.bear.price_target):.2f} (bear gap)")
        elif output.relative_valuation and not output.relative_valuation.unavailable:
            rel = output.relative_valuation
            print(
                f"  Rel targets: bull={rel.bull_target}  "
                f"base={rel.base_target}  "
                f"bear={rel.bear_target}  "
                f"(EV/Rev {rel.ev_revenue_used:.1f}x, {rel.peer_count} peers)"
            )
        else:
            print(f"  Both methods unavailable — dcf_reason={output.dcf.unavailable_reason}")
            if output.relative_valuation:
                print(f"  Rel reason: {output.relative_valuation.skipped_reason}")

        # ── 5. Update memo row in Supabase ───────────────────────────────────
        try:
            client.table("memos").update({
                "memo_json": new_memo_json,
                "valuation_backfilled_at": now_ts,
            }).eq("id", memo_id).execute()
            print(f"  ✓ memo_json updated, valuation_backfilled_at={now_ts[:19]}")
        except Exception as exc:
            print(f"  ERROR updating memo: {exc}\n")
            results.append({
                "ticker": ticker,
                "memo_id": memo_id,
                "outcome": f"ERROR_UPDATE: {exc}",
            })
            continue

        results.append({
            "ticker": ticker,
            "memo_id": memo_id,
            "memo_date": str(memo["date"]),
            "memo_status": memo["status"],
            "old_price_target": old_price_target,
            "old_basis": old_price_target_basis,
            "old_valuation_method": old_valuation_method,
            "new_valuation_method": new_vm,
            "new_price_target": new_price_target,
            "new_basis": new_price_target_basis,
            "outcome": "OK",
        })
        print()

    # ── 6. Summary ────────────────────────────────────────────────────────────
    print("=" * 70)
    print("BACKFILL SUMMARY")
    print("=" * 70)
    print(f"\n{'TICKER':<8} {'OUTCOME':<12} {'OLD METHOD':<12} {'NEW METHOD':<12} "
          f"{'OLD TARGET':>10} {'NEW TARGET':>10}  MEMO DATE")
    print("-" * 80)
    for r in results:
        if r.get("outcome") == "OK":
            old_t = f"${r['old_price_target']}" if r.get("old_price_target") else "None"
            new_t = f"${r['new_price_target']}" if r.get("new_price_target") else "None"
            old_vm = r.get("old_valuation_method") or "pre-gate"
            new_vm = r.get("new_valuation_method", "")
            print(f"{r['ticker']:<8} {'OK':<12} {old_vm:<12} {new_vm:<12} "
                  f"{old_t:>10} {new_t:>10}  {r['memo_date']}  [{r['memo_status']}]")
        else:
            print(f"{r['ticker']:<8} {r.get('outcome', '?')}")

    ok_count = sum(1 for r in results if r.get("outcome") == "OK")
    err_count = len(results) - ok_count
    skip_count = sum(1 for r in results if "SKIP" in r.get("outcome", ""))
    print(f"\nTotal: {ok_count} updated, {skip_count} skipped, {err_count - skip_count} errors")

    print(f"\nClosed positions (not touched): {sorted(closed_tickers)}")
    print("Confirmed: zero closed-position memos updated.")


if __name__ == "__main__":
    main()
