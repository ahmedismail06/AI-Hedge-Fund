"""Smoke test for the ib_insync thread-safety fix.

Reproduces the conditions that caused SEGV core-dumps: many FastAPI worker
threads concurrently calling get_account_summary() while the IB event loop
mutates its internal state. If the fix holds, this completes cleanly with
no crash and consistent NetLiquidation reads across threads.

Run with: python -m scripts.ibkr_thread_safety_smoke
"""
from __future__ import annotations

import concurrent.futures
import sys
import time
from collections import Counter

from backend.broker import ibkr


def hit() -> str:
    s = ibkr.get_account_summary()
    nav = s.get("NetLiquidation")
    return f"nav={nav}"


def main() -> int:
    ibkr.connect()
    print("connected, paper=", ibkr.is_paper())
    print("initial summary:", ibkr.get_account_summary())

    THREADS = 32
    ITERS_PER_THREAD = 50
    start = time.monotonic()
    counts: Counter[str] = Counter()
    errors: list[str] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=THREADS) as pool:
        futs = [pool.submit(hit) for _ in range(THREADS * ITERS_PER_THREAD)]
        for f in concurrent.futures.as_completed(futs):
            try:
                counts[f.result()] += 1
            except Exception as exc:  # pragma: no cover
                errors.append(repr(exc))

    elapsed = time.monotonic() - start
    print(f"completed {THREADS*ITERS_PER_THREAD} calls in {elapsed:.2f}s across {THREADS} threads")
    print("distinct results:", dict(counts))
    if errors:
        print(f"ERRORS ({len(errors)}):")
        for e in errors[:10]:
            print(" ", e)
        return 1

    ibkr.disconnect()
    print("disconnected cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
