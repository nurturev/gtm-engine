-- Migration 006: Scheduled workflows metadata
-- Stores workflow schedule config for display in dashboard.
-- Actual execution is handled by Claude Code's scheduler.

CREATE TABLE scheduled_workflows (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    description     TEXT,
    schedule        TEXT,
    cron_expression TEXT,
    workflow_label  TEXT,
    prompt          TEXT,
    enabled         BOOLEAN DEFAULT TRUE,
    next_run_at     TIMESTAMPTZ,
    last_run_at     TIMESTAMPTZ,
    run_count       INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_scheduled_workflows_tenant ON scheduled_workflows(tenant_id);

ALTER TABLE scheduled_workflows ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON scheduled_workflows
    USING (tenant_id = current_setting('app.current_tenant', true)::text);

ALTER TABLE scheduled_workflows FORCE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE ON scheduled_workflows TO nrev_lite_api;
