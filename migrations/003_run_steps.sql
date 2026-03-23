-- ============================================================
-- Run Steps — Track every MCP tool invocation for workflow logs
-- Version: 003
-- Date: 2026-03-16
-- ============================================================

CREATE TABLE run_steps (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    workflow_id     TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    operation       TEXT,
    provider        TEXT,
    params_summary  JSONB DEFAULT '{}',
    result_summary  JSONB DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'success', 'failed')),
    error_message   TEXT,
    credits_charged NUMERIC(10,2) DEFAULT 0,
    duration_ms     INTEGER,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Index for listing workflows (grouped by workflow_id, ordered by time)
CREATE INDEX idx_run_steps_tenant_workflow ON run_steps(tenant_id, workflow_id, created_at);

-- Index for listing all runs for a tenant, newest first
CREATE INDEX idx_run_steps_tenant_created ON run_steps(tenant_id, created_at DESC);

-- Index for filtering by tool
CREATE INDEX idx_run_steps_tool ON run_steps(tenant_id, tool_name);

-- Row Level Security
ALTER TABLE run_steps ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON run_steps
    USING (tenant_id = current_setting('app.current_tenant', true)::text);
ALTER TABLE run_steps FORCE ROW LEVEL SECURITY;

-- Grant to API role
GRANT SELECT, INSERT, UPDATE, DELETE ON run_steps TO nrev_lite_api;
