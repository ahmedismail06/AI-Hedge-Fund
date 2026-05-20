-- 1. Fix pm_decisions: Add memo_id column and expand execution_status check
ALTER TABLE pm_decisions ADD COLUMN IF NOT EXISTS memo_id UUID REFERENCES memos(id);

ALTER TABLE pm_decisions DROP CONSTRAINT IF EXISTS pm_decisions_execution_status_check;
ALTER TABLE pm_decisions ADD CONSTRAINT pm_decisions_execution_status_check 
CHECK (execution_status IN (
    'SENT_TO_EXECUTION', 'BLOCKED', 'DEFERRED', 'NO_ACTION', 
    'PENDING_HUMAN', 'TRIGGERED_PIPELINE', 'QUEUED_FOR_OPEN'
));

-- 2. Fix orchestrator_log: Expand event_type check for all PM actions
ALTER TABLE orchestrator_log DROP CONSTRAINT IF EXISTS orchestrator_log_event_type_check;
ALTER TABLE orchestrator_log ADD CONSTRAINT orchestrator_log_event_type_check 
CHECK (event_type IN (
    'CYCLE_START', 'CYCLE_END', 'APPROVE_SUCCESS', 'APPROVE_FAILURE', 
    'MODE_CHANGE', 'SUSPENSION_CHANGE', 'CRITICAL_ALERT', 
    'DEPLOY_CASH_ACTION', 'QUEUED_FOR_RESEARCH'
));

-- 3. Fix positions: Expand size_label and exit_action checks
ALTER TABLE positions DROP CONSTRAINT IF EXISTS positions_size_label_check;
ALTER TABLE positions ADD CONSTRAINT positions_size_label_check 
CHECK (size_label IN ('large', 'medium', 'small', 'micro', 'PM_OVERRIDE'));

ALTER TABLE positions DROP CONSTRAINT IF EXISTS positions_exit_action_check;
ALTER TABLE positions ADD CONSTRAINT positions_exit_action_check 
CHECK (exit_action IN ('TRIM', 'CLOSE', 'ADD'));

-- 4. Add pipeline tracking columns to pm_config
ALTER TABLE pm_config ADD COLUMN IF NOT EXISTS pipeline_is_running BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE pm_config ADD COLUMN IF NOT EXISTS pipeline_last_run TIMESTAMPTZ;
