-- Migration: Create financial_models and earnings_events tables
-- These were added to schema.sql but might be missing in the live database.

-- ── Financial Models ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS financial_models (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticker              TEXT NOT NULL,
    run_date            DATE NOT NULL,
    dcf_bull_target     NUMERIC(12,4),
    dcf_base_target     NUMERIC(12,4),
    dcf_bear_target     NUMERIC(12,4),
    wacc                NUMERIC(6,4),
    terminal_growth     NUMERIC(6,4),
    beneish_m_score     NUMERIC(7,4),
    beneish_gate        TEXT,
    accruals_ratio      NUMERIC(8,4),
    quality_grade       TEXT,
    model_json          JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ticker, run_date)
);

CREATE INDEX IF NOT EXISTS financial_models_ticker_idx
    ON financial_models (ticker, run_date DESC);

ALTER TABLE financial_models ENABLE ROW LEVEL SECURITY;
ALTER TABLE financial_models FORCE ROW LEVEL SECURITY;


-- ── EarningsAlpha Events ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS earnings_events (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticker                  TEXT NOT NULL,
    event_date              DATE NOT NULL,
    fiscal_period           TEXT,                   -- e.g. 'Q1 2026'
    reported_eps            NUMERIC(10, 4),
    consensus_eps           NUMERIC(10, 4),
    internal_eps_estimate   NUMERIC(10, 4),
    surprise_pct            NUMERIC(8, 4),          -- (reported - consensus) / |consensus|
    price_reaction_1d       NUMERIC(8, 4),
    price_reaction_5d       NUMERIC(8, 4),
    drift_hold_active       BOOLEAN NOT NULL DEFAULT FALSE,
    drift_hold_until        DATE,
    pre_earnings_signal     TEXT CHECK (pre_earnings_signal IN ('SIZE_UP', 'HOLD', 'REDUCE')),
    run_date                DATE NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ticker, event_date)
);

CREATE INDEX IF NOT EXISTS earnings_events_ticker_idx
    ON earnings_events (ticker, event_date DESC);

CREATE INDEX IF NOT EXISTS earnings_events_drift_idx
    ON earnings_events (ticker, drift_hold_active, drift_hold_until)
    WHERE drift_hold_active = TRUE;

ALTER TABLE earnings_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE earnings_events FORCE ROW LEVEL SECURITY;
