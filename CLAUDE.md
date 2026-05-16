# CLAUDE.md

## Protocols

**CLAUDE.md updates:** Propose changes + show diff → wait for explicit approval before writing.

**Commits:** Ask "Ready to commit Component N — go ahead?" before committing. Group by concern (schemas → logic → agent → API). Short imperative messages. Stage code files only — never .md or system prompt files (except README.md).

## Project

AI-native algo trading for US micro/small-cap equities ($50M–$2B, ≤10 analysts). Broad US equity universe — excludes Pharma/Biotech R&D, Mining/Metals, Oil & Gas, Financial Services, and Utilities; all other sectors included. Claude API (claude-sonnet-4-6) + Interactive Brokers. AI PM Agent (Component 8) drives all decisions; supervised mode as Dashboard fallback. Hard guardrails: 15% position cap, 200% gross ceiling, -10% intraday loss halt.

**Capabilities:** Derived from trailing 30-day NAV via `get_capabilities()` in `backend/capabilities/`.
Tiers: `tier_0` (<$25K, long-only) → `tier_25k` (shorts on $200M+) → `tier_50k` (short factor active) →
`tier_100k` (full L/S, $50M+ universe) → `tier_250k` (alt data flag). No hardcoded phase flags.

## Component Status

| # | Component | Status | Key files |
|---|-----------|--------|-----------|
| 1 | Research Engine | ✅ Done | `backend/agents/research_agent.py`, `backend/memory/` |
| 2 | Screening System | ✅ Done | `backend/agents/screening_agent.py`, `backend/screener/` |
| 3 | Macro Intelligence | ✅ Done | `backend/agents/macro_agent.py`, `backend/macro/` |
| 4 | Portfolio Construction | ✅ Done | `backend/agents/portfolio_agent.py`, `backend/portfolio/` |
| 5 | Risk Management | ✅ Done | `backend/agents/risk_agent.py`, `backend/risk/` |
| 6 | Execution Layer | ✅ Done | `backend/agents/execution_agent.py`, `backend/broker/` |
| 7 | Frontend UI | ✅ Done | `frontend/src/` (React 18 + Vite + Tailwind); auth: `context/AuthContext.jsx` (Supabase admin + guest), `pages/Login.jsx` |
| 8 | AI PM Agent (v2) | ✅ Done | `backend/agents/orchestrator.py`, `backend/agents/pm_prompts/` |
| 9 | Earnings Alpha | ✅ Done | `backend/earnings_alpha/` |
| 10 | Financial Modeling | ✅ Done | `backend/financial_modeling/` |
| 11 | Backtest Engine | ⬜ Not started | — |
| 12 | ML Signal Layer | ⬜ Not started | — |

## Development Setup

```bash
source .venv/bin/activate && uvicorn backend.main:app --reload
cd frontend && npm run dev
```

**Auth:** Admin login uses Supabase Auth (`VITE_SUPABASE_URL` + `VITE_SUPABASE_ANON_KEY` in `frontend/.env.local`). Create your admin user in the Supabase dashboard → Authentication → Users. Guest mode requires no credentials — read-only, all action buttons disabled.

## Notion Sync

Sync on: component implemented → update Component Tracker; new decision → add Decision Log; trade opened/closed → update Trade Journal.

- Component Tracker: `collection://5a9e83e2-e493-419f-9e73-e9937acacf10`
- Decision Log: `collection://0703728b-0895-4a37-9bb8-6206d05f931d`
- Trade Journal: `collection://dd0541dd-773f-4e29-9098-165b4ef496b6`
- Root page: `334c2815-bd4e-812a-9839-d63b6f7504de`

Use `notion-search` by Name → `notion-update-page` with page ID. No duplicate rows.

## Component Briefs

Before editing any file in `.context-config.yml`: `list_components()`, `get_brief("ComponentName")`, `regenerate_brief()` after major changes.

## Task Tracking

`TaskCreate` for any multi-step task. `in_progress` when starting, `completed` immediately when done. `TaskList` at start of resumed conversation.

## Agent Spawning

Spawn proactively (no need to ask first):
- `implement-agent` — new stub agents, memory modules, Pydantic models
- `validate-conventions` — after every new file (always after `implement-agent`)
- `add-supabase-table` — new table needed
- `write-smoke-tests` — after any new module
- `prompt-engineer` — writing/improving LLM system prompts
- `context-engineer` — context growing large; run before large multi-file tasks

## Rules

@.claude/rules/domain-rules.md
@.claude/rules/architecture.md
@.claude/rules/data-sources.md
@.claude/rules/integrations.md
