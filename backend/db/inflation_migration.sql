-- ─────────────────────────────────────────────────────────────────────────────
-- Inflation Engine Redesign: Diagnostics and Consensus tables
-- PR 4 of the inflation engine redesign (docs/inflation_engine_design.md).
-- ─────────────────────────────────────────────────────────────────────────────

-- inflation_consensus: Manual or vendor-provided consensus estimates.
-- Used by the surprise layer (layers/surprise.py).
create table if not exists inflation_consensus (
    id              uuid primary key default gen_random_uuid(),
    release_dt      timestamptz not null,
    indicator       text not null,         -- e.g. 'cpi_yoy', 'pce_yoy'
    expected_value  numeric(6, 4) not null,
    actual_value    numeric(6, 4),         -- Null if not yet released
    source          text not null,         -- 'Manual' | 'TradingEconomics'
    notes           text,
    created_at      timestamptz not null default now(),
    unique (release_dt, indicator)
);

create index if not exists inflation_consensus_dt_idx
    on inflation_consensus (release_dt desc);

-- inflation_diagnostics: Daily audit trail for the layered inflation engine.
-- Stores per-layer scores, confidences, weights, and ICs.
create table if not exists inflation_diagnostics (
    id                  uuid primary key default gen_random_uuid(),
    date                date not null unique,
    score               numeric(5, 4) not null,
    confidence          numeric(5, 4) not null,
    aggregate_method    text not null check (aggregate_method in ('static', 'dynamic')),
    layer_scores        jsonb,             -- {structural: 0.5, momentum: 0.2, ...}
    layer_confidences   jsonb,
    normalised_weights  jsonb,
    layer_ics           jsonb,
    result_json         jsonb not null,    -- Full InflationScoreResult for deep audit
    created_at          timestamptz not null default now()
);

create index if not exists inflation_diagnostics_date_idx
    on inflation_diagnostics (date desc);
