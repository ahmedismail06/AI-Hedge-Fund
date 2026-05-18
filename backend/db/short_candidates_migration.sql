-- Short Pipeline: short_candidates table
-- Run in Supabase SQL editor after deploying the short screening pipeline.
-- Safe to re-run (CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS short_candidates (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_date            date NOT NULL,
    ticker              text NOT NULL,
    short_score         numeric(5,3) NOT NULL,
    deterioration_score numeric(5,3),
    overvaluation_score numeric(5,3),
    accruals_score      numeric(5,3),
    cash_burn_score     numeric(5,3),
    source              text NOT NULL DEFAULT 'watchlist'
                            CHECK (source IN ('watchlist', 'wide_universe')),
    market_cap_m        numeric(12,2),
    sector              text,
    adv_k               numeric(12,2),
    beneish_m_score     numeric(7,4),
    beneish_flag        text CHECK (beneish_flag IN
                            ('EXCLUDED','FLAGGED','CLEAN','INSUFFICIENT_DATA')),
    raw_factors         jsonb,
    queued_for_research boolean NOT NULL DEFAULT false,
    regime              text NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_date, ticker)
);

CREATE INDEX IF NOT EXISTS short_candidates_run_date_score_idx
    ON short_candidates (run_date, short_score DESC);

CREATE INDEX IF NOT EXISTS short_candidates_ticker_idx
    ON short_candidates (ticker, run_date DESC);

CREATE INDEX IF NOT EXISTS short_candidates_queued_idx
    ON short_candidates (queued_for_research, run_date)
    WHERE queued_for_research = TRUE;
