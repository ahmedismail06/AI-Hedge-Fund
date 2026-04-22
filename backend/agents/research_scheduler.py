"""
Research queue poller — fires run_research() for watchlist rows queued after each screen.

Scheduled at 5:00 PM ET Mon–Fri.

Efficiency improvements (2026-04-10):
  - Staleness gate: skips tickers with a memo < 7 days old unless material_event=True
  - Priority ordering: P1 (held+material) → P2 (watchlist+material) → P3 (nightly) → P4 (manual)
  - Daily research cap: hard limit of 10 runs/day, tracked in pm_config; surplus carries to next day
  - update_mode dispatch: held positions without a material event use incremental mode
    (news+transcripts only; skips SEC/Form4/FMP re-fetch and ReAct loop)
"""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

load_dotenv()

from backend.notifications.events import notify_event

logger = logging.getLogger(__name__)

DAILY_RESEARCH_CAP = 10
_STALENESS_DAYS = 7


# ── Helpers ───────────────────────────────────────────────────────────────────

def _needs_research(client, ticker: str) -> bool:
    """Return True if this ticker requires a new research run.

    Priority order:
      1. Most recent memo is DEFERRED + timer expired → True (re-research)
      2. Most recent memo is DEFERRED + timer still active + no material event → False (block)
      3. Most recent memo is < 7 days old + no material event → False (skip)
      4. Otherwise → True
    """
    try:
        result = (
            client.table("memos")
            .select("id,date,status,deferred_until")
            .eq("ticker", ticker)
            .order("date", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.warning("_needs_research: memos query failed for %s — %s; defaulting to True", ticker, exc)
        return True

    if not result.data:
        return True  # no memo at all → full research

    latest = result.data[0]
    memo_status = latest.get("status")

    # ── Deferred memo handling ────────────────────────────────────────────────
    if memo_status == "DEFERRED":
        deferred_until = latest.get("deferred_until")
        if deferred_until:
            try:
                until_dt = datetime.fromisoformat(
                    str(deferred_until).replace("Z", "+00:00")
                )
                if until_dt.tzinfo is None:
                    until_dt = until_dt.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) < until_dt:
                    # Timer still active — check material event bypass
                    try:
                        wl = (
                            client.table("watchlist")
                            .select("material_event")
                            .eq("ticker", ticker)
                            .order("run_date", desc=True)
                            .limit(1)
                            .execute()
                        )
                        if wl.data and wl.data[0].get("material_event", False):
                            logger.info(
                                "_needs_research: %s DEFERRED but material_event=True — bypassing timer",
                                ticker,
                            )
                            return True
                    except Exception:
                        pass
                    logger.info(
                        "_needs_research: %s is DEFERRED until %s — blocking", ticker, deferred_until
                    )
                    return False
            except (ValueError, TypeError):
                pass
        # Timer expired or unparseable → allow re-research
        return True

    # ── Standard staleness gate ───────────────────────────────────────────────
    cutoff = (date.today() - timedelta(days=_STALENESS_DAYS)).isoformat()
    if str(latest.get("date", "")) >= cutoff:
        try:
            wl = (
                client.table("watchlist")
                .select("material_event")
                .eq("ticker", ticker)
                .order("run_date", desc=True)
                .limit(1)
                .execute()
            )
        except Exception as exc:
            logger.warning("_needs_research: watchlist query failed for %s — %s; defaulting to True", ticker, exc)
            return True

        if not wl.data:
            return False  # no watchlist row → conservatively skip

        return bool(wl.data[0].get("material_event", False))

    return True  # old memo → research needed


def _is_held_position(client, ticker: str) -> bool:
    """Return True if ticker has an OPEN position."""
    try:
        result = (
            client.table("positions")
            .select("id")
            .eq("ticker", ticker)
            .eq("status", "OPEN")
            .limit(1)
            .execute()
        )
        return bool(result.data)
    except Exception as exc:
        logger.warning("_is_held_position: query failed for %s — %s", ticker, exc)
        return False


def _get_material_event(client, ticker: str) -> bool:
    """Return the material_event flag for the most recent watchlist row."""
    try:
        result = (
            client.table("watchlist")
            .select("material_event")
            .eq("ticker", ticker)
            .order("run_date", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            return bool(result.data[0].get("material_event", False))
    except Exception as exc:
        logger.warning("_get_material_event: query failed for %s — %s", ticker, exc)
    return False


def _get_and_increment_daily_count(client) -> int:
    """Return the daily research run count AFTER incrementing it.

    Resets automatically when the date changes (new calendar day).
    Persisted to pm_config so it survives process restarts.
    """
    today = date.today().isoformat()
    try:
        row = (
            client.table("pm_config")
            .select("daily_research_count,daily_research_date")
            .eq("id", 1)
            .single()
            .execute()
            .data
        )
    except Exception as exc:
        logger.warning("_get_and_increment_daily_count: read failed — %s; assuming 0", exc)
        return 1

    if not row or str(row.get("daily_research_date") or "") != today:
        try:
            client.table("pm_config").update(
                {"daily_research_count": 1, "daily_research_date": today}
            ).eq("id", 1).execute()
        except Exception as exc:
            logger.warning("_get_and_increment_daily_count: reset write failed — %s", exc)
        return 1

    new_count = row["daily_research_count"] + 1
    try:
        client.table("pm_config").update(
            {"daily_research_count": new_count}
        ).eq("id", 1).execute()
    except Exception as exc:
        logger.warning("_get_and_increment_daily_count: increment write failed — %s", exc)
    return new_count


def _clear_material_event(client, ticker: str, today: str) -> None:
    """Clear material_event flag after research completes."""
    try:
        client.table("watchlist").update(
            {"material_event": False, "material_event_reason": None}
        ).eq("ticker", ticker).eq("run_date", today).execute()
    except Exception as exc:
        logger.warning("_clear_material_event: failed for %s — %s", ticker, exc)


# ── Main queue poller ─────────────────────────────────────────────────────────

def _poll_research_queue() -> list[str]:
    """Build a unified research queue from screener rows and expired deferrals.

    Priority order:
      P1 — watchlist.priority=1  (held positions + material events)
      P2 — memos.status=DEFERRED AND deferred_until <= NOW() (expired deferrals)
      P3 — watchlist.priority>=2 (new candidates + material events)

    Enforces DAILY_RESEARCH_CAP across all sources.  Tickers skipped by the
    staleness gate are cleared from the screener queue; cap overflows carry to
    the next day.  P2 items have no screener queue row to clear.
    """
    from backend.memory.vector_store import _get_client
    from backend.agents.research_agent import run_research

    try:
        client = _get_client()
    except Exception as exc:
        logger.error("_poll_research_queue: Supabase unavailable — %s", exc)
        return []

    today = date.today().isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()

    # ── P1 / P3: screener queue ────────────────────────────────────────────────
    try:
        wl_result = (
            client.table("watchlist")
            .select("ticker,rank,priority,material_event")
            .eq("queued_for_research", True)
            .eq("run_date", today)
            .order("priority", desc=False)
            .order("rank", desc=False)
            .execute()
        )
        watchlist_rows = wl_result.data or []
    except Exception as exc:
        logger.error("_poll_research_queue: watchlist read failed — %s", exc)
        watchlist_rows = []

    p1_rows = [r for r in watchlist_rows if (r.get("priority") or 99) == 1]
    p3_rows = [r for r in watchlist_rows if (r.get("priority") or 99) >= 2]

    # ── P2: expired deferrals ──────────────────────────────────────────────────
    try:
        def_result = (
            client.table("memos")
            .select("ticker,deferred_until")
            .eq("status", "DEFERRED")
            .lte("deferred_until", now_iso)
            .order("deferred_until", desc=False)
            .execute()
        )
        deferred_rows = def_result.data or []
    except Exception as exc:
        logger.warning("_poll_research_queue: deferred memos query failed — %s", exc)
        deferred_rows = []

    # Deduplicate: tickers already in P1 stay in P1; tickers with multiple deferred
    # memos appear only once (earliest deferred_until wins — query is ordered asc).
    p1_tickers = {r["ticker"] for r in p1_rows}
    _seen_p2: set[str] = set()
    p2_rows = []
    for r in deferred_rows:
        t = r["ticker"]
        if t not in p1_tickers and t not in _seen_p2:
            _seen_p2.add(t)
            p2_rows.append(
                {"ticker": t, "rank": 999, "priority": 2,
                 "material_event": False, "_from_deferred": True}
            )
    p2_tickers = {r["ticker"] for r in p2_rows}

    # Remove from P3 any tickers promoted to P2 (expired deferral wins)
    p3_rows = [r for r in p3_rows if r["ticker"] not in p1_tickers and r["ticker"] not in p2_tickers]

    unified_rows = p1_rows + p2_rows + p3_rows

    if not unified_rows:
        logger.info("_poll_research_queue: no tickers queued for research today")
        return []

    from backend.memory.vector_store import store_memo

    logger.info(
        "_poll_research_queue: unified queue — P1=%d, P2=%d, P3=%d",
        len(p1_rows), len(p2_rows), len(p3_rows),
    )

    processed: list[str] = []
    staleness_skipped_wl: list[str] = []  # watchlist rows to clear

    for row in unified_rows:
        ticker = row["ticker"]
        from_deferred = row.get("_from_deferred", False)

        # ── Staleness gate ────────────────────────────────────────────────────
        if not _needs_research(client, ticker):
            logger.info(
                "staleness gate: skipping %s — memo fresh, no material event", ticker,
            )
            if not from_deferred:
                staleness_skipped_wl.append(ticker)
            continue

        # ── Daily cap ─────────────────────────────────────────────────────────
        count = _get_and_increment_daily_count(client)
        if count > DAILY_RESEARCH_CAP:
            logger.warning(
                "_poll_research_queue: daily cap (%d) hit at %s — remaining carry to next day",
                DAILY_RESEARCH_CAP, ticker,
            )
            break

        # ── Determine update_mode ─────────────────────────────────────────────
        is_held = _is_held_position(client, ticker)
        has_material_event = bool(row.get("material_event", False))
        # P2 deferred tickers always get full research (thesis may have materially changed)
        update_mode = is_held and not has_material_event and not from_deferred

        if from_deferred:
            logger.info("_poll_research_queue: %s is P2 (expired deferral) — full research", ticker)
        elif update_mode:
            logger.info("_poll_research_queue: %s is held, no material event — update_mode", ticker)
        else:
            logger.info(
                "_poll_research_queue: %s — full research (held=%s, material_event=%s)",
                ticker, is_held, has_material_event,
            )

        # ── Dequeue from watchlist (screener rows only) ───────────────────────
        if not from_deferred:
            try:
                client.table("watchlist").update({"queued_for_research": False}).eq(
                    "ticker", ticker
                ).eq("run_date", today).execute()
            except Exception as exc:
                logger.warning(
                    "_poll_research_queue: failed to dequeue %s before research — %s; skipping",
                    ticker, exc,
                )
                continue

        # ── Run research ──────────────────────────────────────────────────────
        try:
            memo = run_research(ticker, use_cache=False, update_mode=update_mode)
            processed.append(ticker)
            logger.info("_poll_research_queue: completed %s", ticker)
        except Exception as exc:
            logger.error("_poll_research_queue: run_research(%s) failed — %s", ticker, exc)
            continue

        if not from_deferred:
            _clear_material_event(client, ticker, today)

        # ── PM handoff ────────────────────────────────────────────────────────
        try:
            memo_id = store_memo(ticker, memo)
            if memo_id:
                client.table("memos").update(
                    {"status": "PENDING_PM_REVIEW"}
                ).eq("id", memo_id).execute()
                notify_event("RESEARCH_MEMO_COMPLETED", {
                    "ticker": ticker,
                    "verdict": memo.get("verdict"),
                    "conviction_score": memo.get("conviction_score"),
                    "sector": memo.get("sector"),
                    "price_target": memo.get("price_target"),
                    "requeued_from_deferral": from_deferred,
                })
                logger.info(
                    "_poll_research_queue: memo %s for %s → PENDING_PM_REVIEW%s",
                    memo_id, ticker, " (from deferral)" if from_deferred else "",
                )
            else:
                logger.warning(
                    "_poll_research_queue: store_memo returned no id for %s", ticker,
                )
        except Exception as exc:
            logger.error("_poll_research_queue: PM handoff failed for %s — %s", ticker, exc)

    # Clear queued_for_research for watchlist staleness-skips
    if staleness_skipped_wl:
        try:
            client.table("watchlist").update({"queued_for_research": False}).in_(
                "ticker", staleness_skipped_wl
            ).eq("run_date", today).execute()
            logger.info(
                "_poll_research_queue: cleared staleness-skipped watchlist rows: %s",
                staleness_skipped_wl,
            )
        except Exception as exc:
            logger.warning(
                "_poll_research_queue: failed to clear staleness-skipped flag — %s", exc
            )

    return processed


async def run_research_job() -> None:
    """Async wrapper — isolates exceptions so the scheduler never crashes."""
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _poll_research_queue)
    except Exception as exc:
        logger.error("Research queue job failed: %s", exc, exc_info=True)


def create_research_scheduler() -> AsyncIOScheduler:
    """Return a configured (not yet started) research queue poller.
    Fires at 5:00 PM ET Mon–Fri."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_research_job,
        trigger=CronTrigger(
            hour=17,
            minute=0,
            day_of_week="mon-fri",
            timezone=ZoneInfo("America/New_York"),
        ),
        id="research_queue_daily",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    return scheduler
