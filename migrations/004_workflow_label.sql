-- Add workflow_label column to run_steps for human-readable workflow names
-- Generated: 2026-03-17

ALTER TABLE run_steps ADD COLUMN IF NOT EXISTS workflow_label TEXT;

-- Index for faster dashboard queries grouped by workflow
CREATE INDEX IF NOT EXISTS idx_run_steps_workflow_id ON run_steps (tenant_id, workflow_id);
