-- Component 8 v2 — AI Portfolio Manager Agent
-- Migration: creates pm_config, pm_decisions, pm_calibration tables.
-- The existing orchestrator_config and orchestrator_log tables are left in place.

-- ── PM Configuration ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS pm_config (
    id INTEGER PRIMARY KEY DEFAULT 1,
    mode TEXT NOT NULL DEFAULT 'autonomous'
        CHECK (mode IN ('autonomous', 'supervised')),
    cycle_interval_seconds INTEGER NOT NULL DEFAULT 300,
    daily_loss_halt_triggered BOOLEAN NOT NULL DEFAULT FALSE,
    halted_until TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Ensure only one row ever exists (singleton config)
CREATE UNIQUE INDEX IF NOT EXISTS pm_config_singleton ON pm_config ((1));

-- Default row
INSERT INTO pm_config (id, mode, cycle_interval_seconds, daily_loss_halt_triggered)
VALUES (1, 'autonomous', 300, FALSE)
ON CONFLICT (id) DO NOTHING;


-- ── PM Decisions ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS pm_decisions (
    id SERIAL PRIMARY KEY,
    decision_id TEXT UNIQUE NOT NULL,           -- pm_YYYYMMDD_NNN
    timestamp TIMESTAMPTZ NOT NULL,
    category TEXT NOT NULL
        CHECK (category IN ('NEW_ENTRY', 'EXIT_TRIM', 'REBALANCE', 'CRISIS', 'PRE_EARNINGS')),
    ticker TEXT,                                 -- null for portfolio-level decisions
    decision TEXT NOT NULL,
    action_details JSONB NOT NULL DEFAULT '{}',
    reasoning TEXT NOT NULL,                     -- Claude's visible reasoning
    risk_assessment TEXT NOT NULL DEFAULT '',
    confidence FLOAT CHECK (confidence >= 0.0 AND confidence <= 1.0),
    context_snapshot JSONB NOT NULL,             -- PMContextSnapshot at decision time
    hard_blocks_checked JSONB NOT NULL DEFAULT '{}',
    execution_status TEXT NOT NULL
        CHECK (execution_status IN (
            'SENT_TO_EXECUTION', 'BLOCKED', 'DEFERRED', 'NO_ACTION', 'PENDING_HUMAN', 'TRIGGERED_PIPELINE'
        )),
    human_override JSONB,                        -- null unless human intervened
    outcome JSONB,                               -- populated when position closes (for calibration)
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS pm_decisions_timestamp_idx ON pm_decisions (timestamp DESC);
CREATE INDEX IF NOT EXISTS pm_decisions_ticker_idx ON pm_decisions (ticker) WHERE ticker IS NOT NULL;
CREATE INDEX IF NOT EXISTS pm_decisions_category_idx ON pm_decisions (category);


-- ── PM Calibration ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS pm_calibration (
    id SERIAL PRIMARY KEY,
    decision_id TEXT NOT NULL REFERENCES pm_decisions (decision_id),
    confidence_at_entry FLOAT,
    confidence_at_exit FLOAT,
    holding_period_days INTEGER,
    return_pct FLOAT,
    was_correct BOOLEAN,                         -- did direction match outcome?
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS pm_calibration_decision_idx ON pm_calibration (decision_id);


-- ── Research Efficiency Columns (added 2026-04-10) ───────────────────────────
-- daily_research_count/date: tracks how many research runs fired today (cap=10)
-- av_daily_count/date:       Supabase-persisted Alpha Vantage quota (25/day)

ALTER TABLE pm_config ADD COLUMN IF NOT EXISTS daily_research_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE pm_config ADD COLUMN IF NOT EXISTS daily_research_date  DATE;
ALTER TABLE pm_config ADD COLUMN IF NOT EXISTS av_daily_count       INTEGER NOT NULL DEFAULT 0;
ALTER TABLE pm_config ADD COLUMN IF NOT EXISTS av_daily_date        DATE;
ALTER TABLE pm_config ADD COLUMN IF NOT EXISTS pm_lock_timestamp    TIMESTAMPTZ;
ALTER TABLE pm_config ADD COLUMN IF NOT EXISTS pm_is_running       BOOLEAN NOT NULL DEFAULT FALSE;

-- ── Portfolio value tracking (added for _compute_portfolio_value) ─────────────
ALTER TABLE pm_config ADD COLUMN IF NOT EXISTS cash_balance NUMERIC(12, 2) NOT NULL DEFAULT 0;

-- ── Positions table extensions (added for PM stop-tier + exit routing) ────────
ALTER TABLE positions ADD COLUMN IF NOT EXISTS exit_action        TEXT CHECK (exit_action IN ('TRIM', 'CLOSE'));
ALTER TABLE positions ADD COLUMN IF NOT EXISTS exit_trim_pct      NUMERIC(5, 2);
ALTER TABLE positions ADD COLUMN IF NOT EXISTS stop_tier1         NUMERIC(12, 4);
ALTER TABLE positions ADD COLUMN IF NOT EXISTS stop_tier2         NUMERIC(12, 4);
ALTER TABLE positions ADD COLUMN IF NOT EXISTS stop_tier3         NUMERIC(12, 4);
ALTER TABLE positions ADD COLUMN IF NOT EXISTS next_earnings_date DATE;
