-- Migration: fix pm_calibration and portfolio_metrics gaps
-- Run once in Supabase SQL editor.

-- 1. pm_calibration: drop NOT NULL + FK on decision_id, add ticker column
ALTER TABLE pm_calibration
    ALTER COLUMN decision_id DROP NOT NULL;

ALTER TABLE pm_calibration
    ADD COLUMN IF NOT EXISTS ticker TEXT;

CREATE INDEX IF NOT EXISTS pm_calibration_ticker_idx ON pm_calibration (ticker);
