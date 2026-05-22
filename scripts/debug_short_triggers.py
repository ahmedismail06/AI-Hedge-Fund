import json
import sys
from collections import Counter

sys.path.insert(0, ".")
from backend.screener.short_trigger_scorer import score_triggers, select_qualifying

with open("backend/.short_universe_cache.json") as f:
    d = json.load(f)

rows = d.get("rows") or d.get("tickers") or []
print(f"Universe size: {len(rows)}")

scored = score_triggers(rows)
qualifying = select_qualifying(scored)

print(f"Qualifying (>=3 triggers, >=4 evaluable): {len(qualifying)}")
for r in qualifying:
    print(f"  {r.ticker}: {r.trigger_count} triggers {r.triggers_fired}")

counts = Counter(r.trigger_count for r in scored)
evaluable_counts = Counter(len(r.triggers_evaluated) for r in scored)
print("Trigger count distribution:", dict(sorted(counts.items())))
print("Evaluable count distribution:", dict(sorted(evaluable_counts.items())))

# Show a sample of what's blocking qualification
blocked = [r for r in scored if len(r.triggers_evaluated) < 4]
print(f"\nTickers blocked by MIN_EVALUABLE (<4 evaluable): {len(blocked)}")
if blocked:
    sample = blocked[:3]
    for r in sample:
        print(f"  {r.ticker}: evaluated={r.triggers_evaluated} count={r.trigger_count}")
