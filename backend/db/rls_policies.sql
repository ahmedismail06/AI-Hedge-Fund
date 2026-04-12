-- ─────────────────────────────────────────────────────────────────────────────
-- Row Level Security: lock down all tables to service_role only
--
-- HOW THIS WORKS
-- ──────────────
-- Supabase has two client keys:
--   anon key        → maps to the `anon` Postgres role (public-facing, low-trust)
--   service_role key → has BYPASSRLS privilege, ignores all RLS policies entirely
--
-- The backend always uses SUPABASE_KEY (service_role), so it is unaffected.
-- Enabling RLS with NO policies for anon/authenticated blocks all public access.
-- FORCE ROW LEVEL SECURITY also blocks the table owner (postgres) as a safety net.
--
-- Run this once in the Supabase SQL editor.
-- It is idempotent — safe to re-run.
-- ─────────────────────────────────────────────────────────────────────────────

-- Research Engine
ALTER TABLE memos               ENABLE ROW LEVEL SECURITY;
ALTER TABLE memos               FORCE ROW LEVEL SECURITY;

-- Agentic RAG vector store
ALTER TABLE document_chunks     ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_chunks     FORCE ROW LEVEL SECURITY;

-- Screening System
ALTER TABLE watchlist           ENABLE ROW LEVEL SECURITY;
ALTER TABLE watchlist           FORCE ROW LEVEL SECURITY;

-- Macro Intelligence Engine
ALTER TABLE macro_briefings     ENABLE ROW LEVEL SECURITY;
ALTER TABLE macro_briefings     FORCE ROW LEVEL SECURITY;

-- Portfolio Construction
ALTER TABLE positions           ENABLE ROW LEVEL SECURITY;
ALTER TABLE positions           FORCE ROW LEVEL SECURITY;

-- Risk Agent
ALTER TABLE risk_alerts         ENABLE ROW LEVEL SECURITY;
ALTER TABLE risk_alerts         FORCE ROW LEVEL SECURITY;

ALTER TABLE portfolio_metrics   ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolio_metrics   FORCE ROW LEVEL SECURITY;

-- Execution Agent
ALTER TABLE orders              ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders              FORCE ROW LEVEL SECURITY;

ALTER TABLE fills               ENABLE ROW LEVEL SECURITY;
ALTER TABLE fills               FORCE ROW LEVEL SECURITY;

-- Research Efficiency: event calendar
ALTER TABLE ticker_events       ENABLE ROW LEVEL SECURITY;
ALTER TABLE ticker_events       FORCE ROW LEVEL SECURITY;

-- Orchestrator (Component 8)
-- These tables are created separately; ALTER is a no-op if they don't exist yet.
ALTER TABLE orchestrator_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE orchestrator_config FORCE ROW LEVEL SECURITY;

ALTER TABLE orchestrator_log    ENABLE ROW LEVEL SECURITY;
ALTER TABLE orchestrator_log    FORCE ROW LEVEL SECURITY;

-- Portfolio Manager Agent (pm_migration.sql)
ALTER TABLE pm_config           ENABLE ROW LEVEL SECURITY;
ALTER TABLE pm_config           FORCE ROW LEVEL SECURITY;

ALTER TABLE pm_decisions        ENABLE ROW LEVEL SECURITY;
ALTER TABLE pm_decisions        FORCE ROW LEVEL SECURITY;

ALTER TABLE pm_calibration      ENABLE ROW LEVEL SECURITY;
ALTER TABLE pm_calibration      FORCE ROW LEVEL SECURITY;


-- ─────────────────────────────────────────────────────────────────────────────
-- RPC function security
--
-- match_document_chunks queries document_chunks which now has RLS enabled.
-- When called via service_role the caller has BYPASSRLS so it works fine.
-- Redefine as SECURITY DEFINER so that even if called via anon it runs as
-- the function owner (postgres), but the RETURN is still filtered by RLS on
-- the caller side — i.e., anon callers get 0 rows back.
--
-- Safest option: keep SECURITY INVOKER (default) so the caller's own RLS
-- applies. anon role has no policies → gets nothing. service_role bypasses.
-- Nothing to change here — just documenting the behaviour.
-- ─────────────────────────────────────────────────────────────────────────────

-- ─────────────────────────────────────────────────────────────────────────────
-- Verification query (run after applying to confirm RLS is active)
-- ─────────────────────────────────────────────────────────────────────────────
-- SELECT tablename, rowsecurity, forcerowsecurity
-- FROM   pg_tables
-- WHERE  schemaname = 'public'
-- ORDER  BY tablename;
--
-- Every row should show: rowsecurity = true, forcerowsecurity = true
-- ─────────────────────────────────────────────────────────────────────────────
