# AI Hedge Fund

An AI-native algorithmic trading system for US micro/small-cap equities. Uses Claude (claude-sonnet-4-6) for qualitative research synthesis and Interactive Brokers for execution. All trades require human approval by default.

## Universe

- Market cap: $50M–$2B
- Max 5 sell-side analysts
- Min $500K average daily volume
- Sectors: SaaS, Healthcare, Industrials

## Architecture

```
Orchestrator
├── Macro Agent          — regime classification (Risk-On / Risk-Off / Transitional / Stagflation)
├── Screening Agent      — runs daily at 4PM ET, scores ~800 stocks, upserts top candidates to watchlist
├── Research Agent       — generates InvestmentMemo per ticker (verdict: LONG / SHORT / AVOID, conviction 0–10)
├── Portfolio Agent      — Kelly-based position sizing, exposure caps by regime
├── Execution Agent      — IBKR order routing (limit / VWAP based on ADV%)
└── Risk Agent           — continuous 60s monitor, 3-tier stop structure
```

## Status

| Component | Status |
|-----------|--------|
| Research Engine (Component 1) | Done |
| Screening System (Component 2) | Done |
| Macro Agent | Stub |
| Portfolio Agent | Stub |
| Risk Agent | Stub |
| Execution Agent | Stub |
| Financial Modeling (DCF + Beneish) | Stub |
| Earnings Alpha | Not started |
| Backtest Engine | Not started |
| ML Signal Layer | Not started (requires 50+ closed trades) |

## Screening Pipeline

1. Universe builder — Polygon.io paginated list filtered by cap, analyst count, SIC code (24h disk cache)
2. Beneish M-score gate — hard exclusion if M-score > −1.78 (fraud risk)
3. Factor scoring — Quality (50%), Value (30%), Momentum (20%)
4. Composite scorer — average-rank percentile normalization; value is sector-relative
5. Regime adjustment — weights shift across 4 macro regimes
6. Discrete adjustments — insider buying +0.3, short interest bonus, Risk-Off/Stagflation caps
7. Top candidates queued for Research Agent; results upserted to Supabase `watchlist`

## Research Engine

Hybrid retrieval pipeline:
- pgvector index of SEC 10-K/10-Q filings and earnings transcripts (BAAI/bge-base-en-v1.5, 768 dims)
- ReAct tool-use loop (≤10 turns) for targeted document retrieval
- Claude synthesizes into a structured `InvestmentMemo` with `variant_perception` and `repricing_catalyst` (both schema-enforced required fields)

## Data Sources

| Source | Used for |
|--------|----------|
| Polygon.io | Universe, prices, ADV, news |
| SEC EDGAR | 10-K, 10-Q, Form 4 insider buying |
| yfinance | Short interest, analyst estimates, earnings dates |
| Alpha Vantage | Earnings call transcripts |
| FRED | Fed funds rate, CPI, yield curve, PMI |

## Setup

```bash
# Backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in API keys
uvicorn backend.main:app --reload

# Frontend
cd frontend && npm install && npm run dev
```

Required keys: `ANTHROPIC_API_KEY`, `POLYGON_API_KEY`, `SUPABASE_URL`, `SUPABASE_KEY`

Optional: `ALPHA_VANTAGE_API_KEY` (transcript formatting), `FRED_API_KEY`, `FMP_API_KEY`

## API Endpoints

```
POST /screening/run        — trigger a full screening run
GET  /screening/watchlist  — return current watchlist from Supabase
```

## Tests

```bash
pytest tests/ -q   # 177 smoke tests
```

## Position Sizing

- 25% fractional Kelly; conviction score used as win-rate proxy
- Large = 8%, Medium = 5%, Small = 2%, Micro = 1% of portfolio
- Hard cap: 15% per position
- Stop tiers: −8% / −15% / −20% (tightened in Risk-Off)

## Modes

- **Supervised** (default): all trades require human approval via dashboard
- **Autonomous**: auto-approves conviction ≥ 8.5; suspends if daily drawdown > 5%
