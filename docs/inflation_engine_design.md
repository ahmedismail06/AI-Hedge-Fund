# Inflation Engine Redesign — Design Doc

**Status:** Draft for review · **Author:** Claude+Ahmed · **Date:** 2026-05-21
**Replaces:** `backend/macro/scorer.py::_score_inflation` (current discrete-bucket scorer)

---

## 1. Problem statement

Today inflation is one of four dimensional signals feeding regime classification. The current implementation has six concrete failure modes:

| Failure | Concrete evidence | Why it matters |
|---|---|---|
| **Sticky output** | inflation_score has held in [0.73, 0.80] across the last 21 daily briefings even as CPI series gained ~25bps and breakevens moved 15bps. | Drives the macro engine to default-Transitional, which crushes PM `portfolio_fit` confidence. |
| **Discrete buckets** | `_cpi_like()` is a 5-step function with cliffs at 1/2/3/5% YoY. A CPI move from 3.0→2.9 changes the per-series signal from 0.5→0.0 — a 50% jump from a 10bp data move. | Low information density; large local sensitivity at thresholds, zero sensitivity elsewhere. |
| **Mixed frequency mishandled** | FRED CPI/PCE/PPI release monthly; breakeven_5y is daily. They are averaged equally despite the daily series being the *only* one updating between releases. | Daily score is dominated by stale monthly snapshots; the one real signal carrying information per day is diluted by 4×. |
| **Equal weighting** | `np.mean(signals)` with no per-indicator weight, no confidence weighting, no recency weighting. | A 1-day-old breakeven counts the same as a 28-day-old CPI print. |
| **No momentum** | YoY only. No MoM, no 3m-annualised, no acceleration/deceleration, no regression slope. | Misses inflection points — score keeps reading "hot" for months after CPI peaks because YoY decays slowly. |
| **No surprise** | No actual-vs-consensus, no economic-surprise index, no asymmetric reaction. | Misses one of the highest-information events in macro (CPI/PCE prints relative to consensus). |

**Decision settled with user (2026-05-21):**
- Code location → **full `backend/macro/inflation_engine/` package** (Option B).
- Data sources → **Polygon + FMP (both paid) + FRED + yfinance fallback**; surprise-layer interface in v1, consensus integration when a vendor is wired.
- Delivery → **5 PRs**, smallest first.
- Doc-then-code: this doc is the gate.

---

## 2. Design overview

### 2.1 Layered factor architecture

```
                    InflationScore (continuous ∈ [-1, +1])
                                  ▲
                                  │  weighted aggregate
        ┌───────────────┬─────────┴─────────┬───────────────────┐
        │               │                   │                   │
   Structural       Momentum         Market Expectations    Surprise
   Layer            Layer            Layer                  Layer
   (level)          (Δ, slope)       (forward-looking)      (vs consensus)
        │               │                   │                   │
   CPI/Core/PPI    3m annualised,       Breakevens,         Actual vs
   /PCE YoY,       MoM annualised,      inflation swaps,    expected
   trimmed-mean    rolling z-score      DXY-adj oil,        prints
   CPI, sticky     of Δ, regression     gasoline futures,   (uses Released
   CPI             slope, accel         copper, 5y5y,       Calendar +
                                        WTI               consensus interface)
        │               │                   │                   │
        ▼               ▼                   ▼                   ▼
        each layer is independently normalized to [-1, +1]
        each layer has its own contributors list for attribution
```

Each layer is a pure function with the signature:

```python
def compute(snapshot: IndicatorSnapshot, history: IndicatorHistory) -> LayerOutput
```

where `LayerOutput` carries:
- `score: float` ∈ [-1, +1]
- `confidence: float` ∈ [0, 1]  ← shrinks when inputs are stale, sparse, or contradictory
- `contributors: list[Contribution]`  ← per-indicator score + weight + raw value + age
- `notes: list[str]`  ← human-readable diagnostics

This is what makes the engine *testable* and *attributable* — every score decomposes cleanly.

### 2.2 Continuous normalization toolkit

A small library of bounded transforms (in `inflation_engine/normalize.py`):

| Function | Use case | Formula |
|---|---|---|
| `tanh_norm(x, scale)` | Bounded soft-clip with smooth tails | `tanh((x - center) / scale)` |
| `rolling_zscore(x, window, history)` | Distance from recent regime | `(x - μ_window) / σ_window`, then `tanh` to bound |
| `percentile_rank(x, history)` | Robust to outliers, distribution-free | `rank(x) / n`, then map `[0,1]` → `[-1,+1]` |
| `winsorize(x, p_low, p_high, history)` | Cap extreme tails before normalising | clip at empirical quantiles |
| `vol_adjusted(x, history)` | Scale by realised volatility | `x / σ_window` then `tanh` |

**Replaces** the current step functions. A CPI YoY of 3.0 → 2.9 will now produce a smooth 5bp change in score, not a cliff.

### 2.3 Mixed-frequency handling

Every indicator carries metadata:

```python
@dataclass
class IndicatorMeta:
    series_id: str                  # e.g., "CPIAUCSL"
    native_frequency: str           # "daily" | "weekly" | "monthly" | "quarterly"
    last_release_dt: datetime       # actual release timestamp from FRED/vendor
    next_expected_release: datetime # from release calendar
    staleness_days: int             # today - last_release_dt
    is_stale: bool                  # staleness_days > 1.5 × native_frequency
```

Per-indicator scoring respects this:
- **Daily indicators** (breakevens, oil, DXY, treasury yields): scored every run.
- **Monthly indicators** (CPI/Core/PPI/PCE, trimmed-mean): scored from latest release; carry an *age-decay* on confidence (1.0 right after release, 0.5 at 28 days, then sharply drops if a release is late).
- **Stale data** doesn't get dropped — it gets *confidence-weighted down* so a fresh daily breakeven dominates a 27-day-old CPI in the daily score, but the CPI still anchors the level.

The aggregator combines layer scores using `confidence × weight`, then renormalizes.

### 2.4 Daily-sensitive market inputs

New ingestion (in `inflation_engine/sources/market_inputs.py`):

| Indicator | Source | Polygon ticker / FMP endpoint | Layer |
|---|---|---|---|
| WTI crude | Polygon | `USO` (ETF) or `CL` futures if available | Market Expectations |
| Brent crude | Polygon | `BNO` | Market Expectations |
| Gasoline (RBOB) | Polygon | `UGA` | Market Expectations |
| Copper | Polygon | `CPER` or `HG` | Market Expectations |
| Commodity composite | Polygon | `DBC` or `GSG` | Market Expectations |
| US 10Y yield | FRED `DGS10` | already wired | Market Expectations |
| 5Y5Y forward | FRED `T5YIFR` | new | Market Expectations |
| Sticky-price CPI | FRED `STICKCPIM157SFRBATL` | new | Structural |
| Trimmed-mean PCE | FRED `PCETRIM12M159SFRBDAL` | new | Structural |
| Inflation swaps | TBD | not in repo today — interface only | Market Expectations |

Each source goes through the same `IndicatorMeta` wrapper so the engine doesn't care where data came from.

### 2.5 Surprise layer (interface in v1, data later)

```python
@dataclass
class ConsensusExpectation:
    indicator: str
    expected_value: float
    actual_value: Optional[float]
    release_dt: datetime
    source: str  # "TradingEconomics" | "Econoday" | "Manual" | ...

class ConsensusSource(Protocol):
    def get_expectations(self, since: datetime) -> list[ConsensusExpectation]: ...
```

v1 ships:
- The `ConsensusSource` Protocol.
- A `ManualConsensusSource` that reads from a Supabase table `inflation_consensus` (insert by hand for backtesting).
- A `NullConsensusSource` (always empty) — default, makes the surprise layer return `score=0, confidence=0`.
- The aggregator drops the surprise layer when `confidence=0`.

When a paid vendor (Trading Economics, Econoday, or even FMP if their economic-calendar endpoint covers it) is wired, one adapter slots in without touching the rest of the engine.

### 2.6 Aggregation

```python
@dataclass
class AggregationWeights:
    structural: float = 0.40
    momentum: float = 0.25
    market_expectations: float = 0.25
    surprise: float = 0.10

# Active weighting: w_i × confidence_i, then renormalize
```

- Defaults above (configurable via `inflation_engine/config.py`).
- When a layer has `confidence=0`, weight is redistributed proportionally to remaining layers — surprise layer can be entirely absent without breaking anything.
- An optional **dynamic weighting** mode shifts weight toward whichever layer has the highest signal-to-noise over a trailing window — flagged off by default in v1; framework is in place.

### 2.7 Observability

Every `score_inflation()` call returns an `InflationScoreResult` carrying:

```python
@dataclass
class InflationScoreResult:
    score: float                         # the final [-1, +1]
    confidence: float                    # final aggregate confidence
    layers: dict[str, LayerOutput]       # per-layer decomposition
    contributors_top5: list[Contribution]  # top contributors by |weighted_signal|
    aggregate_method: str                # "static" | "dynamic"
    stale_warnings: list[str]
    diagnostics: dict                    # raw values, ages, weights — full attribution
```

Persisted (optionally) to a new `inflation_diagnostics` Supabase table on each run, joinable to `macro_briefings.date`. Dashboards can show:
- Score decomposition stacked bar (4 layers' signed contributions).
- Top-5 contributors with arrows.
- Staleness heatmap.

---

## 3. Module layout

```
backend/macro/inflation_engine/
├── __init__.py                # public API: score_inflation() entry point
├── config.py                  # AggregationWeights, thresholds, registry config
├── types.py                   # IndicatorMeta, LayerOutput, Contribution, InflationScoreResult
├── normalize.py               # tanh_norm, rolling_zscore, percentile_rank, winsorize, vol_adjusted
├── ingestion/
│   ├── __init__.py
│   ├── fred_inflation.py      # CPI/Core/PPI/PCE/breakevens/sticky/trimmed-mean
│   ├── market_inputs.py       # oil, gasoline, copper, commodity index, yields (Polygon)
│   ├── fmp_calendar.py        # FMP economic calendar endpoint (consensus prints)
│   └── consensus.py           # ConsensusSource Protocol + Null/Manual implementations
├── transforms/
│   ├── __init__.py
│   ├── momentum.py            # 3m-annualised, MoM-annualised, regression slope, accel
│   └── synthetic.py           # DXY-adjusted oil, gasoline-implied CPI proxy, etc.
├── layers/
│   ├── __init__.py
│   ├── structural.py          # level-based; YoY-anchored, sticky/trimmed-mean-adjusted
│   ├── momentum.py            # rate-of-change layer
│   ├── market_expectations.py # breakevens, oil/copper, 5y5y, yields, swaps
│   └── surprise.py            # actual vs consensus
├── aggregation.py             # combine layers → final score + diagnostics
├── registry.py                # factor registry — allows adding/removing factors via config
└── diagnostics.py             # InflationScoreResult formatting, optional Supabase persist
```

**Entry point** (`backend/macro/inflation_engine/__init__.py`):

```python
def score_inflation(
    snapshot: IndicatorSnapshot,
    history: IndicatorHistory,
    weights: AggregationWeights | None = None,
    consensus_source: ConsensusSource | None = None,
) -> InflationScoreResult: ...
```

`backend/macro/scorer.py::_score_inflation()` becomes a thin shim that:
1. Builds an `IndicatorSnapshot` + `IndicatorHistory` from existing `RawIndicators`/`FredBlock`/`MarketBlock`.
2. Calls `score_inflation(...)`.
3. Returns `result.score` for the existing API contract.
4. Logs `result` for observability.

Existing callers (`score_indicators()`, tests, dashboards) **don't change** in PR1-3. The expanded API surfaces in PR4 when diagnostics are wired up.

---

## 4. PR sequencing

### PR 1 — Continuous normalization (~1 day)
**Goal:** smooth out the discrete-bucket cliffs. Same indicators, same architecture, no new data.

- `inflation_engine/types.py` + `normalize.py` (just the toolkit, no layers yet).
- Rewrite `_score_inflation()` internals to use `tanh_norm` per series instead of step functions.
- Per-series anchor/scale parameters live in `config.py`:
  ```python
  CPI_YOY_CENTER = 2.0   # Fed target
  CPI_YOY_SCALE  = 2.0   # ±2% from target = ±tanh(1) ≈ ±0.76
  ```
- Tests: prove monotonicity, boundedness, smoothness at threshold points (CPI 2.99→3.01 should change score by <1bp, not 50bp).
- Acceptance: today's inflation_score should move from 0.78 to ~0.55–0.65 (CPI still hot, but no longer slammed against the bucket cliff).

### PR 2 — Mixed-frequency handling (~1 day)
**Goal:** stale monthly series no longer dilute fresh daily ones.

- `IndicatorMeta` + age-decay confidence weighting in `aggregation.py`.
- Extend `FredBlock`/`MarketBlock` to carry per-series `last_release_dt`.
- `score_inflation()` returns a `confidence` field; aggregator uses `confidence × weight`.
- Tests: hold all values constant, advance days; daily-input weight increases as monthly inputs age.
- Acceptance: inflation_score now varies day-to-day even within a CPI release cycle (target: ≥10bp daily stddev vs ~0bp today).

### PR 3 — Layer split + daily-sensitive market inputs (~1 day)
**Goal:** the actual layered architecture goes in.

- All four layer modules implemented (`structural`, `momentum`, `market_expectations`, `surprise` interface).
- New ingestion: oil/gasoline/copper via Polygon ETF tickers; sticky/trimmed-mean CPI via FRED; 5y5y forward.
- Replace `_score_inflation()` shim to call layered `score_inflation()`.
- Surprise layer wired to `NullConsensusSource` (returns confidence=0, layer drops).
- Tests: each layer in isolation, then full aggregation under known scenarios.

### PR 4 — Surprise data + diagnostics + dynamic weighting (~1 day)
**Goal:** turn on the surprise layer with `ManualConsensusSource`; wire full attribution; activate dynamic IC-based weighting.

- New Supabase table `inflation_consensus`: `(release_dt, indicator, expected_value, actual_value, source, notes)`. Populated by hand for backtesting; vendor adapters slot in later via the Protocol.
- `ManualConsensusSource` reads the table; falls back to `confidence=0` when no consensus row exists for a given indicator/release.
- `aggregation.py`: implement IC-based dynamic weighting with the 0.5 floor + 90-day cold-start fallback.
- `diagnostics.py` persists `InflationScoreResult` to a new `inflation_diagnostics` table per run; includes per-layer IC and effective weights for auditability.
- New `/api/macro/inflation/diagnostics` endpoint (read-only) for dashboard use.

### PR 5 — Tests + observability polish (~½ day)
**Goal:** lock in regression coverage and dashboard surface area.

- End-to-end regression: replay last 60 days of macro_briefings → compare new inflation_score series to old; produce a delta report.
- Stability tests: simulate ±10% noise on each input, score variance stays bounded.
- Add a tiny frontend panel (or just a JSON endpoint) showing layer decomposition.

Each PR is independently mergeable. After PR 1+2 alone, the stickiness problem is largely fixed.

---

## 5. Backward-compatibility guarantees

- `_score_inflation(ind: RawIndicators) -> float` keeps its signature through all 5 PRs. Existing callers (`score_indicators`, tests) are untouched.
- `macro_briefings.inflation_score` column unchanged; values change in magnitude/responsiveness but stay in [-1, +1].
- Macro_agent LLM prompt unchanged in PR 1-3; in PR 4 we add an optional `inflation_diagnostics` block to the user message if the user wants Claude to see the decomposition.
- New Supabase tables (`inflation_consensus`, `inflation_diagnostics`) are additive; no migrations to existing tables.

## 6. Resolved open questions (2026-05-21)

1. **FMP economic calendar — NOT in user's plan.** User's FMP tier covers profile/ratios/prices/fundamentals/news/crypto-forex only; no `/economic-calendar` with consensus estimates. **Decision:** PR4 ships `ManualConsensusSource` (Supabase `inflation_consensus` table, populated by hand) as the default. Vendor adapters (Trading Economics, Econoday, BLS-scraping) remain a future option behind the `ConsensusSource` Protocol — no rework needed when one is added.
2. **Polygon commodity tickers — confirmed.** User confirmed `/v2/snapshot/locale/us/markets/stocks/tickers` is on their plan. ETF proxies are the right call: `USO` (WTI), `UGA` (gasoline), `CPER` (copper), `DBC` (commodity composite), `BNO` (Brent). We hit the bulk snapshot endpoint with all five tickers in one call to stay well under the 300 req/min budget.
3. **Dynamic weighting — ENABLED in v1.** Not behind a flag.
   - **Implementation:** each layer carries a rolling **information coefficient (IC)** — the rank correlation between the layer's score at time `t-N` and the realised change in headline CPI YoY between `t-N` and `t`. We use a 90-day rolling window (will grow to 180/365 as history accumulates).
   - **Aggregation:** effective weight per layer = `base_weight × confidence × max(0.5, IC_normalised)`. The 0.5 floor keeps layers with bad recent IC from being silenced (avoids over-fitting to short windows). Weights are renormalized to sum to 1.0 after the multiplier.
   - **Cold start:** for the first 90 days of operation, `IC_normalised = 1.0` everywhere, so dynamic weighting collapses to static — no behaviour change until enough history exists.
   - **Diagnostics:** the per-layer IC is logged on every run and surfaced in `InflationScoreResult.diagnostics`, so it's easy to see whether a layer is actually predictive or being downweighted for a reason.
4. **Backtest impact — none.** User confirmed no existing inflation backtest fixtures to preserve. New scorer goes live in PR1 with no feature-flag fallback; old `_cpi_like()` step function is removed cleanly.

## 7. Out of scope (explicitly)

- Wage growth / labor-cost inflation indicators (separate workstream).
- Inflation expectations from consumer surveys (UMich, NY Fed). Could be added later via a new ingestion module without touching the architecture.
- Cross-currency inflation comparisons.
- Multi-country inflation regimes.

---

**Sign-off needed:** answer #1–4 above, then I start PR1.
