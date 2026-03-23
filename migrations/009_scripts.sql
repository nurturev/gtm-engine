-- 009_scripts.sql — Reusable parameterized workflow scripts
-- Depends on: 001_tenants.sql

BEGIN;

CREATE TABLE IF NOT EXISTS scripts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           TEXT NOT NULL REFERENCES tenants(id),
    name                TEXT NOT NULL,
    slug                TEXT NOT NULL,
    description         TEXT,
    parameters          JSONB NOT NULL DEFAULT '[]',
    steps               JSONB NOT NULL DEFAULT '[]',
    source_workflow_id  TEXT,
    tags                TEXT[] NOT NULL DEFAULT '{}',
    run_count           INTEGER NOT NULL DEFAULT 0,
    last_run_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(tenant_id, slug)
);

-- RLS
ALTER TABLE scripts ENABLE ROW LEVEL SECURITY;
CREATE POLICY scripts_tenant ON scripts
    USING (tenant_id = current_setting('app.tenant_id'));

-- Indexes
CREATE INDEX IF NOT EXISTS idx_scripts_tenant ON scripts(tenant_id);
CREATE INDEX IF NOT EXISTS idx_scripts_slug ON scripts(tenant_id, slug);

-- Grants
GRANT SELECT, INSERT, UPDATE, DELETE ON scripts TO nrev_lite_api;

COMMIT;
