-- Screener status tracking in pm_config
-- screener_is_running: TRUE while run_screening() is executing
-- screener_last_run:   timestamp of the most recent completed run
ALTER TABLE pm_config ADD COLUMN IF NOT EXISTS screener_is_running BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE pm_config ADD COLUMN IF NOT EXISTS screener_last_run   TIMESTAMPTZ;
