-- Short Pipeline v2: extend short_candidates for event-driven + trigger-count flow.
-- Safe to re-run. Apply after short_candidates_migration.sql.

-- ── 1. Widen source CHECK constraint ──────────────────────────────────────────
-- Legacy values (watchlist, wide_universe) preserved for backward compat; new
-- values cover the event-driven queue path (BENEISH_EXCLUDED, NEWS_*,
-- SEC_RESTATEMENT, MANUAL) and the dedicated trigger scorer (TRIGGER_SCORER).

ALTER TABLE short_candidates DROP CONSTRAINT IF EXISTS short_candidates_source_check;
ALTER TABLE short_candidates ADD CONSTRAINT short_candidates_source_check
    CHECK (source IN (
        'watchlist',          -- legacy
        'wide_universe',      -- legacy
        'TRIGGER_SCORER',     -- dedicated short universe + trigger-count scorer
        'BENEISH_EXCLUDED',   -- long-screener Beneish hard exclusion
        'NEWS_GUIDANCE_CUT',  -- news fetcher: guidance cut detected
        'SEC_RESTATEMENT',    -- SEC fetcher: 10-K/10-Q restatement
        'SHORT_REPORT',       -- short-seller report mention
        'MANUAL'              -- user-flagged via dashboard
    ));

-- ── 2. Priority (1=urgent, 10=backlog) ────────────────────────────────────────
ALTER TABLE short_candidates
    ADD COLUMN IF NOT EXISTS priority smallint NOT NULL DEFAULT 5
        CHECK (priority BETWEEN 1 AND 10);

-- ── 3. Evidence payload (jsonb) ───────────────────────────────────────────────
-- For TRIGGER_SCORER:  {"triggers_fired": [...], "trigger_count": N, "raw": {...}}
-- For BENEISH_EXCLUDED: {"m_score": ..., "missing_fields": [...]}
-- For NEWS_*/SEC_*:    {"source_url": ..., "headline": ..., "excerpt": ...}
ALTER TABLE short_candidates
    ADD COLUMN IF NOT EXISTS evidence jsonb;

-- ── 4. Trigger count + which triggers fired ──────────────────────────────────
ALTER TABLE short_candidates
    ADD COLUMN IF NOT EXISTS trigger_count smallint;
ALTER TABLE short_candidates
    ADD COLUMN IF NOT EXISTS triggers_fired text[];

-- ── 5. Loosen NOT NULL on legacy scorer columns ───────────────────────────────
-- The percentile-rank scorer wrote these; TRIGGER_SCORER/event paths don't.
ALTER TABLE short_candidates ALTER COLUMN short_score DROP NOT NULL;

-- ── 6. Index for queue polling by priority ───────────────────────────────────
CREATE INDEX IF NOT EXISTS short_candidates_queued_priority_idx
    ON short_candidates (queued_for_research, priority, run_date DESC)
    WHERE queued_for_research = TRUE;
