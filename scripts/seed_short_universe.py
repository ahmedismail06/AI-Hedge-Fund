"""
One-shot: seed the short-universe JSON cache.

The daily short screener reads backend/.short_universe_cache.json; if that
file is missing the TRIGGER_SCORER path produces 0 candidates. Normal
operation is to wait for the weekly `short_universe_weekly` APScheduler
job (Sun 02:00 ET) to rebuild it. Run this script when you need the cache
populated immediately — e.g. first deploy or after deleting the file.

Runtime: ~30–60 min. Progress is logged every 200 tickers across three
phases (cap/sector → ADV → enrich). Safe to interrupt and re-run; nothing
is written until the rebuild completes successfully.

Usage:
    python scripts/seed_short_universe.py
"""
from __future__ import annotations

import logging
import os
import sys
import time

# ── ensure repo root on sys.path ─────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from backend.screener.short_universe import _fetch_universe_fresh, _save_cache


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    log = logging.getLogger("seed_short_universe")

    started = time.monotonic()
    log.info("Seeding short-universe cache — expect ~30–60 min")
    rows = _fetch_universe_fresh()
    elapsed_m = (time.monotonic() - started) / 60.0

    if not rows:
        log.error("Rebuild returned 0 rows after %.1fm — cache NOT written", elapsed_m)
        return 1

    _save_cache(rows)
    log.info("Seeded %d rows in %.1fm", len(rows), elapsed_m)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
