-- Research Engine: memos table
-- Run this in the Supabase SQL editor to create the table.

create table if not exists memos (
    id              uuid primary key default gen_random_uuid(),
    ticker          text not null,
    date            date not null,
    verdict         text not null check (verdict in ('LONG', 'SHORT', 'AVOID')),
    conviction_score numeric(4, 2) not null check (conviction_score >= 0 and conviction_score <= 10),
    memo_json       jsonb not null,
    raw_docs        jsonb,
    status          text not null default 'PENDING'
                        check (status in ('PENDING', 'APPROVED', 'REJECTED', 'WATCHLIST')),
    created_at      timestamptz not null default now()
);

-- Index for fast lookups by ticker (latest memo)
create index if not exists memos_ticker_created_idx on memos (ticker, created_at desc);

-- Index for watchlist queries
create index if not exists memos_status_idx on memos (status);

-- ─────────────────────────────────────────────────────────────────────────────
-- Agentic RAG: vector store for SEC filings and earnings transcripts
-- Prerequisite: run `CREATE EXTENSION IF NOT EXISTS vector;` once in Supabase
--               SQL editor before running this block.
-- ─────────────────────────────────────────────────────────────────────────────

-- document_chunks: pgvector table for agentic retrieval
-- doc_type allowed values: '10-K' | '10-Q' | 'transcript'
create table if not exists document_chunks (
    id           uuid primary key default gen_random_uuid(),
    ticker       text not null,
    doc_type     text not null,       -- '10-K' | '10-Q' | 'transcript'
    section      text,                -- 'Item 1' | 'Item 7' | 'Q4_2025' | null
    chunk_index  integer not null,
    content      text not null,
    token_count  integer,
    embedding    vector(768),         -- BAAI/bge-base-en-v1.5 output dims
    filing_date  date,
    created_at   timestamptz not null default now(),
    unique (ticker, doc_type, section, chunk_index)
);

-- IVFFlat index for fast approximate cosine similarity search
create index if not exists document_chunks_embedding_idx
    on document_chunks using ivfflat (embedding vector_cosine_ops) with (lists = 100);

-- Index for ticker + doc_type filtering (WHERE clause in match_document_chunks)
create index if not exists document_chunks_ticker_idx
    on document_chunks (ticker, doc_type);

-- ─────────────────────────────────────────────────────────────────────────────
-- Screening System: watchlist table
-- Stores daily screener output — ranked candidates + audit trail for EXCLUDED.
-- ─────────────────────────────────────────────────────────────────────────────

create table if not exists watchlist (
    id              uuid primary key default gen_random_uuid(),
    run_date        date not null,
    ticker          text not null,
    composite_score numeric(5,3) not null,
    quality_score   numeric(5,3) not null,
    value_score     numeric(5,3) not null,
    momentum_score  numeric(5,3) not null,
    rank            integer not null,
    market_cap_m    numeric(12,2),
    adv_k           numeric(12,2),
    sector          text,
    regime          text not null,
    beneish_m_score numeric(7,4),
    beneish_flag    text check (beneish_flag in ('EXCLUDED', 'FLAGGED', 'CLEAN', 'INSUFFICIENT_DATA')),
    insider_signal  boolean default false,
    raw_factors     jsonb,
    queued_for_research boolean default false,
    created_at      timestamptz not null default now(),
    unique (run_date, ticker)
);

create index if not exists watchlist_run_date_rank_idx on watchlist (run_date, rank asc);
create index if not exists watchlist_ticker_idx on watchlist (ticker, run_date desc);

-- ─────────────────────────────────────────────────────────────────────────────
-- Macro Intelligence Engine: macro_briefings table
-- Stores daily MacroBriefing output from the Macro Agent (7AM ET).
-- Downstream agents read current regime via SELECT ... ORDER BY date DESC LIMIT 1.
-- One authoritative record per trading day (UNIQUE on date).
-- ─────────────────────────────────────────────────────────────────────────────

create table if not exists macro_briefings (
    id                  uuid primary key default gen_random_uuid(),
    date                date not null,
    regime              text not null
                            check (regime in ('Risk-On', 'Risk-Off', 'Transitional', 'Stagflation')),
    regime_score        numeric(6, 2) not null,
    previous_regime     text
                            check (previous_regime is null or previous_regime in ('Risk-On', 'Risk-Off', 'Transitional', 'Stagflation')),
    regime_changed      boolean not null default false,
    growth_score        numeric(5, 4) not null,
    inflation_score     numeric(5, 4) not null,
    fed_score           numeric(5, 4) not null,
    stress_score        numeric(5, 4) not null,
    regime_confidence   numeric(4, 2) not null,
    override_flag       boolean not null default false,
    override_reason     text,
    qualitative_summary text not null,
    key_themes          jsonb not null default '[]'::jsonb,
    portfolio_guidance  text not null,
    indicator_scores    jsonb not null default '[]'::jsonb,
    sector_tilts        jsonb,
    upcoming_events     jsonb,
    briefing_json       jsonb not null,
    created_at          timestamptz not null default now(),
    unique (date)
);

create index if not exists macro_briefings_date_idx
    on macro_briefings (date desc);

create index if not exists macro_briefings_created_idx
    on macro_briefings (created_at desc);

-- ─────────────────────────────────────────────────────────────────────────────
-- Portfolio Construction: positions table
-- Stores every sizing recommendation produced by the Portfolio Construction Agent
-- (Component 4). One row per position decision; status tracks the full lifecycle
-- from PENDING_APPROVAL through OPEN to CLOSED.
-- direction allowed values: 'LONG' | 'SHORT'
-- status allowed values: 'PENDING_APPROVAL' | 'APPROVED' | 'REJECTED' | 'OPEN' | 'CLOSED'
-- size_label allowed values: 'large' | 'medium' | 'small' | 'micro'
-- ─────────────────────────────────────────────────────────────────────────────

create table if not exists positions (
    id                  uuid primary key default gen_random_uuid(),
    ticker              text not null,
    memo_id             uuid references memos(id),
    direction           text not null check (direction in ('LONG', 'SHORT')),
    status              text not null default 'PENDING_APPROVAL'
                            check (status in ('PENDING_APPROVAL', 'APPROVED', 'REJECTED', 'OPEN', 'CLOSED')),
    conviction_score    numeric(4, 2) not null,
    kelly_fraction      numeric(6, 4) not null,
    dollar_size         numeric(12, 2) not null,
    share_count         numeric(10, 2) not null,
    size_label          text not null check (size_label in ('large', 'medium', 'small', 'micro')),
    pct_of_portfolio    numeric(6, 4) not null,
    entry_price         numeric(12, 4),
    current_price       numeric(12, 4),
    stop_loss_price     numeric(12, 4),
    target_price        numeric(12, 4),
    risk_reward_ratio   numeric(6, 2),
    sizing_rationale    text not null,
    correlation_flag    boolean not null default false,
    correlation_note    text,
    sector              text,
    regime_at_sizing    text not null,
    portfolio_state_after jsonb,
    pnl                 numeric(12, 2),
    pnl_pct             numeric(8, 4),
    opened_at           timestamptz,
    closed_at           timestamptz,
    created_at          timestamptz not null default now()
);

create index if not exists positions_ticker_idx on positions (ticker);
create index if not exists positions_status_idx on positions (status);
create index if not exists positions_created_idx on positions (created_at desc);

-- ─────────────────────────────────────────────────────────────────────────────
-- RPC function for cosine similarity search
create or replace function match_document_chunks(
    query_embedding  vector(768),
    filter_ticker    text,
    filter_doc_types text[],
    match_count      int default 4
)
returns table (
    id          uuid,
    ticker      text,
    doc_type    text,
    section     text,
    chunk_index integer,
    content     text,
    token_count integer,
    filing_date date,
    similarity  float
)
language sql stable
as $$
    select
        dc.id,
        dc.ticker,
        dc.doc_type,
        dc.section,
        dc.chunk_index,
        dc.content,
        dc.token_count,
        dc.filing_date,
        1 - (dc.embedding <=> query_embedding) as similarity
    from document_chunks dc
    where dc.ticker = filter_ticker
      and (filter_doc_types is null or dc.doc_type = any(filter_doc_types))
    order by dc.embedding <=> query_embedding
    limit match_count;
$$;
