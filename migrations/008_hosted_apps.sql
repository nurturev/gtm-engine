-- Migration 008: Hosted Apps
-- Static HTML/CSS/JS apps backed by persistent datasets

BEGIN;

CREATE TABLE hosted_apps (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    dataset_ids UUID[] NOT NULL DEFAULT '{}',
    app_token TEXT NOT NULL,
    app_token_hash TEXT NOT NULL,
    files JSONB NOT NULL DEFAULT '{}',
    entry_point TEXT DEFAULT 'index.html',
    status TEXT DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, slug)
);

CREATE INDEX idx_hosted_apps_tenant ON hosted_apps(tenant_id);
CREATE INDEX idx_hosted_apps_token_hash ON hosted_apps(app_token_hash);

-- Row-Level Security
ALTER TABLE hosted_apps ENABLE ROW LEVEL SECURITY;
CREATE POLICY hosted_apps_tenant_isolation ON hosted_apps
    USING (tenant_id = current_setting('app.current_tenant', true));

-- Grants
GRANT SELECT, INSERT, UPDATE, DELETE ON hosted_apps TO nrev_lite_api;

COMMIT;
