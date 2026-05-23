# AI Hedge Fund

An AI-native algorithmic trading system for US micro/small-cap equities. Uses Claude (`claude-sonnet-4-6`) for qualitative research synthesis and Interactive Brokers for live execution. All trades require human approval by default.

---

## Universe

- Market cap: $50M–$2B
- Max 10 sell-side analysts
- Min $500K average daily volume
- Sectors: SaaS, Healthcare Services, Industrials, Consumer (excludes Pharma/Biotech R&D, Mining/Metals, Oil & Gas, Financial Services, Utilities)

---

## Architecture

```
Orchestrator (AI Portfolio Manager)
    ├─→ runs 5-min cycle ──→ Claude Reasoning (Sonnet 4.6) ──→ categorizes actionable items
    │       ├─→ NEW_ENTRY ────→ Portfolio Agent (Quant Sizing) ──→ APPROVED / PENDING
    │       ├─→ EXIT_TRIM ────→ Updates positions (CLOSE / TRIM / ADD)
    │       ├─→ REBALANCE ────→ Exposure drift management (TRIM / RAISE_CASH / DEPLOY_CASH)
    │       ├─→ PRE_EARNINGS ─→ Risk mitigation (SIZE_UP / TRIM / EXIT)
    │       └─→ CRISIS ───────→ Emergency halts & liquidations
    ├─→ hard gates (Python) ──→ Enforces 15% position cap, 200% gross, −10% daily loss
    ├─→ reactive inputs:
    │       ├─→ Risk Agent ──→ handle_critical_alert() ──→ Immediate CRISIS cycle
    │       └─→ Macro Agent ─→ handle_regime_change() ──→ Immediate REBALANCE cycle
    └─→ scheduled crons:
            ├─→  7:00 AM ET ──→ Macro Agent ──→ Regime shift detection
            ├─→  4:00 PM ET ──→ Screening Agent ──→ Universe scoring → watchlist upsert
            ├─→  4:15 PM ET ──→ Earnings ticker events → PM cycle
            ├─→  8:15 PM ET ──→ Research Agent ──→ Queued memo generation
            ├─→ 10:00 PM ET ──→ Nightly risk metrics (Sharpe, VaR, drawdown, beta)
            └─→  every 60s ──→ Risk Monitor + every 5m → Execution Cycle
```

**Key data flows:**
- **Macro Agent** → publishes regime to Supabase (`macro_briefings`) → all agents read it to adjust thresholds
- **Research Agent** → stores memos in Supabase (`memos`); pgvector semantic search via `match_document_chunks` RPC (BAAI/bge-base-en-v1.5, 768 dims)
- **Screening Agent** → reads regime from Supabase, adjusts factor weights/thresholds; Beneish M-score hard gate pre-score
- **Portfolio Agent** → reads regime to apply exposure caps (Risk-On: 150% gross; Risk-Off: 80% gross)
- **Risk Agent** → reads regime to tighten stop thresholds; CRITICAL flag triggers immediate reactive orchestrator cycle
- **Financial Modeling** → DCF scenarios + Beneish M-score injected into memo context before Research Agent
- **Capabilities Module** → `get_capabilities()` called by any agent gating on NAV; snapshots written to `capability_snapshots` on each PM cycle

---

## Component Status

| Component | Status |
|-----------|--------|
| Research Engine (1) | Done |
| Screening System (2) | Done |
| Macro Agent (3) | Done |
| Portfolio Agent (4) | Done |
| Risk Agent (5) | Done |
| Execution Agent (6) | Done |
| Orchestrator / AI PM Agent (8) | Done |
| Earnings Alpha (9) | Done |
| Financial Modeling — DCF + Beneish (10) | Done |
| Capabilities / NAV Gating | Done |
| Backtest Engine (11) | Not started |
| ML Signal Layer (12) | Not started — requires 50+ closed trades |

---

## Backend Module Map

```
backend/
├── agents/              # orchestrator.py, execution_agent.py, macro_agent.py,
│                        # portfolio_agent.py, research_agent.py, risk_agent.py,
│                        # screening_agent.py, short_screening_agent.py, research_scheduler.py
│                        # pm_prompts/ (system prompts), pm_schemas.py
├── api/                 # FastAPI routers: capabilities, earnings_alpha, execution,
│                        # financial_modeling, macro, market, orchestrator, pm, portfolio, risk
├── broker/              # IBKR bridge: ibkr (connection), order_builder, order_manager,
│                        # fill_recorder, schemas
├── capabilities/        # NAV-driven feature gating: resolver.py (get_capabilities),
│                        # nav_tracker.py (30d trailing avg, 1h cache), schemas.py
├── db/                  # schema.sql, rls_policies.sql, pm_migration.sql
├── earnings_alpha/      # Pre/post-earnings sizing: runner.py, drift_manager.py,
│                        # estimate_comparator.py, schemas.py
├── fetchers/            # sec_fetcher, news_fetcher, transcript_fetcher, form4_fetcher,
│                        # fmp_fetcher, earnings_reactions
├── financial_modeling/  # DCF (bull/base/bear), earnings quality (Beneish M-score),
│                        # relative_valuation.py, runner.py, schemas.py
├── macro/               # indicators/ (fed_scraper, fred_fetcher, market_fetcher),
│                        # scorer.py, scheduler.py
├── memory/              # vector_store.py (pgvector CRUD), document_indexer.py
│                        # (section-aware SEC chunker)
├── models/              # Pydantic models: macro_briefing, memo, position, risk, watchlist
├── notifications/       # events.py, slack.py
├── portfolio/           # sizing_engine.py (Kelly), correlation.py,
│                        # exposure_tracker.py, schemas.py
├── risk/                # alerts, exposure_monitor, metrics, monitor, notifier, stop_loss
└── screener/            # universe.py, scorer.py, scheduler.py, short_universe.py,
                         # short_trigger_scorer.py
                         # factors/: quality, value, momentum, earnings_quality, short_interest
```

---

## Screening Pipeline

1. **Universe builder** — Polygon.io paginated list, filtered by cap/analyst count/SIC code (24h disk cache)
2. **Beneish M-score gate** — hard exclusion if M-score > −1.78 (fraud risk); flag in memo if > −2.22
3. **Factor scoring** — Quality 50%, Value 30%, Momentum 20% (regime-adjusted)
   - Quality sub-weights: ROIC 25%, FCF conversion 20%, gross margin 15%, revenue growth 15%, debt/equity 15%, EPS beat rate 10%
4. **Composite scorer** — average-rank percentile normalization; Value is sector-relative
5. **Regime adjustment** — weights shift across 4 macro regimes (Risk-On / Risk-Off / Transitional / Stagflation / Constructive)
6. **Discrete adjustments** — insider buying +0.3 (CEO/CFO open-market buys via EDGAR Form 4), short-interest bonus
7. **Short screening** — parallel pipeline identifies short candidates (chained short screener)
8. Results upserted to Supabase `watchlist`; top candidates queued for Research Agent

Threshold: composite score ≥ 6.5 to qualify.

---

## Research Engine

Hybrid retrieval-augmented generation pipeline:

- **Ingestion** — SEC 10-K/10-Q filings and earnings call transcripts chunked with section-aware splitter and embedded with BAAI/bge-base-en-v1.5 (~400 MB, downloaded once)
- **Retrieval** — pgvector index in Supabase; semantic search via `match_document_chunks` RPC
- **Synthesis** — ReAct tool-use loop (≤10 turns) for targeted retrieval; Claude synthesizes into a structured `InvestmentMemo`

`InvestmentMemo` schema-enforced required fields: `variant_perception`, `repricing_catalyst`, `conviction_score_rationale`, `valuation_note`, `cash_runway_months`. Without `variant_perception`, conviction cannot exceed 6.0 and verdict cannot be LONG.

---

## Financial Modeling

Runs after screener, before Research Agent:

- **DCF** — three scenarios (bull/base/bear) with discount rate and terminal growth sensitivity
- **Beneish M-score** — accrual-quality earnings fraud detection (also gates the screener)
- **Relative valuation** — peer-multiple comparison via Supabase `peer_multiples` table
- Results stored in `financial_models` table; injected into Research Agent memo context

---

## Earnings Alpha

Pre/post-earnings positioning rules:

- **Estimate comparator** — flags estimate revisions vs. consensus
- **Drift manager** — drift-hold logic (holds position through earnings drift window)
- Integrated into orchestrator `PRE_EARNINGS` decision path

---

## Position Sizing & Risk

**Sizing (25% fractional Kelly):**
- Conviction score (0–10) used as win-rate proxy until 50+ closed trades
- Large = 8%, Medium = 5%, Small = 2%, Micro = 1% of portfolio
- Hard cap: 15% per position (code-level block)

**Order routing:**
- < 1% ADV → limit order
- 1–5% ADV → VWAP over 30 minutes
- > 5% ADV → full-day VWAP

**Stop structure (3 tiers):**
| Tier | Risk-On | Risk-Off |
|------|---------|----------|
| Position stop | −8% | −5% |
| Strategy stop | −15% | −10% |
| Portfolio stop | −20% | −15% |

**Nightly metrics** (10 PM ET Mon–Fri): Sharpe, Sortino, max drawdown, VaR (95%, historical simulation), beta, Calmar ratio.

---

## Macro Regimes

Five states: **Risk-On** | **Risk-Off** | **Transitional** | **Stagflation** | **Constructive**

Regime published to Supabase `macro_briefings` at 7 AM ET. All downstream agents read this table to adjust their factor weights, exposure caps, and stop thresholds.

---

## Data Sources

| Source | Used by | Key data | Env var |
|--------|---------|----------|---------|
| Polygon.io | Universe builder, news, risk monitor | Stock list, prices, ADV, news, VIX | `POLYGON_API_KEY` |
| SEC EDGAR | SEC fetcher, insider buying | 10-K, 10-Q, Form 4 | — (free) |
| Alpha Vantage | Transcript fetcher | Earnings call transcripts | `ALPHA_VANTAGE_API_KEY` |
| FRED API | Macro indicators | Fed funds rate, CPI, yield curve, PMI | `FRED_API_KEY` |
| yfinance | Macro, financial modeling | SPX, VIX, short interest, EPS estimates, earnings dates | — (free) |
| Fed website | Macro agent | FOMC statements (scraped) | — |

**LLM:** `claude-sonnet-4-6` via the `anthropic` SDK. Search `CLAUDE SWAP` comments to find all model swap points.

**Broker:** IBKR via `ib_insync`. `ENV=paper` → port 7497; `ENV=live` → port 7496. TWS/IB Gateway must be running.

---

## Frontend

React 18 + Vite, Tailwind CSS, Recharts, React Router v6, Axios.

| Page | Description |
|------|-------------|
| Dashboard (Command Center) | Portfolio overview, PM cycle status, recent decisions |
| Signals (Book) | Screener watchlist, research memos, conviction scores |
| Portfolio | Positions, pending approvals, equity curve |
| Risk Engine | Alerts, stop levels, exposure monitor |
| Macro | Regime briefing, macro indicators, history |
| Execution | Orders, fills, IBKR connection status |

Deployed at: `https://ai-hedge-fund-rosy.vercel.app`

---

## API Reference

### Research
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/research/{ticker}` | Run full research pipeline for a ticker (`?use_cache=true` to skip re-fetch) |
| `POST` | `/research/run-queued` | Process today's queued-for-research tickers |
| `GET` | `/research/{ticker}/latest` | Most recent memo for a ticker |
| `GET` | `/research/history` | Last 50 memos (summary fields) |
| `GET` | `/research/watchlist` | All APPROVED and WATCHLIST memos |
| `POST` | `/research/{memo_id}/status` | Update memo status (APPROVED / REJECTED / WATCHLIST / DEFERRED) |

### Screening
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/screening/run` | Trigger full screening run (optionally pass `?regime=`) |
| `GET` | `/screening/watchlist` | Today's watchlist (`?all_time=true` for cross-date top scores) |

### PM / Orchestrator
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/pm/status` | Current PM agent state |
| `GET` | `/pm/decisions` | Recent PM decisions |
| `GET` | `/pm/decisions/{id}` | Single decision detail |
| `POST` | `/pm/override/{id}` | Override a PM decision |
| `POST` | `/pm/override/close/{ticker}` | Force-close a position |
| `POST` | `/pm/override/halt` | Emergency halt |
| `POST` | `/pm/override/resume` | Resume after halt |
| `GET` | `/pm/calibration` | Kelly calibration state |
| `POST` | `/pm/cycle/run` | Manually trigger a PM cycle |
| `GET/POST` | `/pm/config` | Read/update PM configuration |
| `GET` | `/orchestrator/status` | Orchestrator cycle status |
| `POST` | `/orchestrator/cycle/run` | Trigger orchestrator cycle |
| `GET/POST` | `/orchestrator/mode` | Read/set supervised vs autonomous mode |
| `GET` | `/orchestrator/log` | Recent cycle log |

### Portfolio
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/portfolio/size` | Compute Kelly position size |
| `GET` | `/portfolio/positions` | Current live positions |
| `GET` | `/portfolio/pending` | Pending approval queue |
| `GET` | `/portfolio/exposure` | Gross/net exposure by regime |
| `POST` | `/portfolio/approve/{id}` | Approve a pending position |
| `POST` | `/portfolio/reject/{id}` | Reject a pending position |
| `GET` | `/portfolio/history` | Historical positions |
| `GET` | `/portfolio/equity-curve` | Equity curve data |

### Risk
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/risk/alerts` | All risk alerts |
| `GET` | `/risk/alerts/critical` | Critical-only alerts |
| `POST` | `/risk/alerts/{id}/resolve` | Resolve an alert |
| `GET` | `/risk/metrics` | Latest portfolio metrics |
| `GET` | `/risk/metrics/history` | Historical metrics |
| `POST` | `/risk/metrics/run` | Trigger nightly metrics computation |
| `GET` | `/risk/status` | Risk monitor status |
| `GET` | `/risk/stops` | Current stop levels per position |
| `POST` | `/risk/monitor/run` | Manually trigger risk monitor |

### Macro
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/macro/briefing` | Latest macro briefing |
| `GET` | `/macro/regime` | Current regime |
| `GET` | `/macro/history` | Regime history |
| `GET` | `/macro/indicators` | Raw indicator values |
| `GET` | `/macro/inflation/diagnostics` | Inflation factor diagnostics |
| `POST` | `/macro/run` | Trigger macro analysis |

### Execution
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/execution/orders` | All orders |
| `GET` | `/execution/orders/{id}` | Single order detail |
| `GET` | `/execution/fills` | Recent fills |
| `POST` | `/execution/cancel/{id}` | Cancel an order |
| `GET` | `/execution/status` | IBKR connection status |
| `POST` | `/execution/cycle/run` | Trigger execution cycle |

### Financial Modeling
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/financial-modeling/{ticker}/latest` | Latest model for a ticker |
| `POST` | `/financial-modeling/run/{ticker}` | Run DCF + Beneish for a ticker |

### Earnings Alpha
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/earnings-alpha/{ticker}/latest` | Latest earnings alpha analysis |
| `GET` | `/earnings-alpha/{ticker}/drift-hold` | Drift-hold status |
| `POST` | `/earnings-alpha/run/{ticker}` | Run earnings alpha pipeline |

### Capabilities / NAV
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/capabilities` | Current capability tier |
| `GET` | `/capabilities/nav` | Current NAV |
| `GET` | `/capabilities/history` | NAV and tier history |

---

## Supabase Schema

Key tables: `memos`, `document_chunks`, `watchlist`, `positions`, `orders`, `fills`, `macro_briefings`, `risk_alerts`, `portfolio_metrics`, `pm_config`, `pm_decisions`, `pm_calibration`, `earnings_events`, `financial_models`, `peer_multiples`, `short_candidates`, `capability_snapshots`, `account_snapshots`.

All agents read/write via `supabase-py`. `vector_store.py` uses a **fresh client per call** (not a singleton).

---

## Setup

```bash
# 1. Backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in API keys

# 2. Run backend (from repo root)
WATCHFILES_IGNORE_PATHS=".venv" uvicorn backend.main:app --reload --reload-dir backend

# 3. Frontend
cd frontend && npm install && npm run dev
```

**Required env vars:**

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Claude API |
| `POLYGON_API_KEY` | Universe, prices, news |
| `SUPABASE_URL` | Database |
| `SUPABASE_KEY` | Database |

**Optional env vars:**

| Variable | Purpose |
|----------|---------|
| `ALPHA_VANTAGE_API_KEY` | Earnings call transcripts (25 req/day free) |
| `FRED_API_KEY` | Macro indicators |
| `FMP_API_KEY` | Legacy — not active |

**Broker:** TWS or IB Gateway must be running on the local machine before starting the backend.
- Paper trading: port 7497
- Live trading: port 7496

---

## Modes

| Mode | Behavior |
|------|----------|
| **Supervised** (default) | All trades require human approval via dashboard |
| **Autonomous** | Auto-approves trades with conviction ≥ 8.5; suspends on daily drawdown > 5% |

Toggle via the dashboard. Autonomous mode requires an explicit confirmation click.

---

## Tests

```bash
pytest tests/ -q
```
