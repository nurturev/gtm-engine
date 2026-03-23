-- nrev-lite Database Schema — Add workflow_id to credit ledger
-- Run: psql -U nrev_lite -d nrev_lite -f migrations/012_ledger_workflow.sql

-- Add workflow_id to credit_ledger so transactions can be grouped by workflow
ALTER TABLE credit_ledger ADD COLUMN IF NOT EXISTS workflow_id TEXT;

-- Index for fast workflow-grouped queries
CREATE INDEX IF NOT EXISTS idx_credit_ledger_workflow ON credit_ledger (tenant_id, workflow_id)
    WHERE workflow_id IS NOT NULL;
