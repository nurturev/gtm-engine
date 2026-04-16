-- ============================================================
-- Combined Production Migration: 001-008 + 015
-- Database: nrv | Role: nrv_api
-- Run as: postgres (superuser) against the `nrv` database
-- ============================================================


-- ============================================================
-- 000: Schema Migrations Tracking
-- ============================================================

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO schema_migrations (version, filename) VALUES
    ('001', '001_initial.sql'),
    ('002', '002_domain_index.sql'),
    ('003', '003_run_steps.sql'),
    ('004', '004_workflow_label.sql'),
    ('005', '005_datasets.sql'),
    ('006', '006_scheduled_workflows.sql'),
    ('007', '007_dashboard_datasets.sql'),
    ('008', '008_hosted_apps.sql'),
    ('015', '015_refresh_tokens_subject_id.sql')
ON CONFLICT (version) DO NOTHING;


-- ============================================================
-- 001: Initial Schema
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- TENANTS & USERS

CREATE TABLE tenants (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    domain          TEXT,
    gtm_stage       TEXT,
    goals           TEXT[],
    settings        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE users (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email           TEXT UNIQUE NOT NULL,
    name            TEXT,
    google_id       TEXT UNIQUE,
    avatar_url      TEXT,
    role            TEXT DEFAULT 'member' CHECK (role IN ('owner', 'admin', 'member')),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    last_login_at   TIMESTAMPTZ
);

-- refresh_tokens created here will be dropped and recreated by 015 below

CREATE TABLE refresh_tokens (
    id              SERIAL PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash      TEXT NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_refresh_tokens_user ON refresh_tokens(user_id);
CREATE INDEX idx_refresh_tokens_hash ON refresh_tokens(token_hash);

-- CONTACTS

CREATE TABLE contacts (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email           TEXT,
    name            TEXT,
    first_name      TEXT,
    last_name       TEXT,
    title           TEXT,
    phone           TEXT,
    linkedin        TEXT,
    company         TEXT,
    company_domain  TEXT,
    location        TEXT,
    icp_score       NUMERIC(5,2),
    enrichment_sources JSONB DEFAULT '{}',
    extensions      JSONB DEFAULT '{}',
    tags            TEXT[],
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, email)
);

-- COMPANIES

CREATE TABLE companies (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    domain          TEXT,
    name            TEXT,
    industry        TEXT,
    employee_count  INTEGER,
    employee_range  TEXT,
    revenue_range   TEXT,
    funding_stage   TEXT,
    total_funding   NUMERIC,
    location        TEXT,
    description     TEXT,
    technologies    TEXT[],
    enrichment_sources JSONB DEFAULT '{}',
    extensions      JSONB DEFAULT '{}',
    tags            TEXT[],
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, domain)
);

-- SEARCH RESULTS

CREATE TABLE search_results (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    query_hash      TEXT NOT NULL,
    operation       TEXT NOT NULL,
    params          JSONB NOT NULL,
    result_count    INTEGER,
    results         JSONB NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ENRICHMENT LOG

CREATE TABLE enrichment_log (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    execution_id    TEXT NOT NULL,
    batch_id        TEXT,
    operation       TEXT NOT NULL,
    provider        TEXT NOT NULL,
    key_mode        TEXT NOT NULL CHECK (key_mode IN ('platform', 'byok')),
    params          JSONB NOT NULL,
    result          JSONB,
    status          TEXT NOT NULL CHECK (status IN ('success', 'failed', 'cached')),
    error_message   TEXT,
    credits_charged NUMERIC(10,2) DEFAULT 0,
    duration_ms     INTEGER,
    cached          BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- CREDIT SYSTEM

CREATE TABLE credit_ledger (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    entry_type      TEXT NOT NULL CHECK (entry_type IN ('credit', 'debit', 'hold', 'release')),
    amount          NUMERIC(10,2) NOT NULL,
    balance_after   NUMERIC(10,2) NOT NULL,
    operation       TEXT,
    reference_id    TEXT,
    description     TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE credit_balances (
    tenant_id       TEXT PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    balance         NUMERIC(10,2) NOT NULL DEFAULT 0,
    spend_this_month NUMERIC(10,2) NOT NULL DEFAULT 0,
    month_reset_at  TIMESTAMPTZ NOT NULL DEFAULT (date_trunc('month', NOW()) + INTERVAL '1 month'),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- PAYMENTS

CREATE TABLE payments (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    amount_usd      NUMERIC(10,2) NOT NULL,
    credits         NUMERIC(10,2) NOT NULL,
    package         TEXT,
    stripe_status   TEXT NOT NULL CHECK (stripe_status IN ('pending', 'completed', 'failed')),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

-- TENANT KEYS (BYOK)

CREATE TABLE tenant_keys (
    id              SERIAL PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    provider        TEXT NOT NULL,
    encrypted_key   BYTEA NOT NULL,
    key_hint        TEXT,
    status          TEXT DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, provider)
);

-- DASHBOARDS

CREATE TABLE dashboards (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    s3_path         TEXT NOT NULL,
    data_queries    JSONB,
    read_token_hash TEXT NOT NULL,
    refresh_interval INTEGER DEFAULT 3600,
    password_hash   TEXT,
    status          TEXT DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'deleted')),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, name)
);

-- INDEXES (001)

CREATE INDEX idx_contacts_tenant ON contacts(tenant_id);
CREATE INDEX idx_contacts_email ON contacts(tenant_id, email);
CREATE INDEX idx_contacts_company ON contacts(tenant_id, company_domain);
CREATE INDEX idx_contacts_icp ON contacts(tenant_id, icp_score DESC);
CREATE INDEX idx_contacts_created ON contacts(tenant_id, created_at DESC);

CREATE INDEX idx_companies_tenant ON companies(tenant_id);
CREATE INDEX idx_companies_domain ON companies(tenant_id, domain);
CREATE INDEX idx_companies_industry ON companies(tenant_id, industry);

CREATE INDEX idx_search_results_tenant ON search_results(tenant_id);
CREATE INDEX idx_search_results_hash ON search_results(tenant_id, query_hash);

CREATE INDEX idx_enrichment_log_tenant ON enrichment_log(tenant_id, created_at DESC);
CREATE INDEX idx_enrichment_log_exec ON enrichment_log(execution_id);
CREATE INDEX idx_enrichment_log_batch ON enrichment_log(batch_id) WHERE batch_id IS NOT NULL;

CREATE INDEX idx_credit_ledger_tenant ON credit_ledger(tenant_id, created_at DESC);
CREATE INDEX idx_credit_ledger_ref ON credit_ledger(reference_id) WHERE reference_id IS NOT NULL;

CREATE INDEX idx_payments_tenant ON payments(tenant_id);

CREATE INDEX idx_dashboards_tenant ON dashboards(tenant_id);

-- ROW LEVEL SECURITY (001)

ALTER TABLE contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE companies ENABLE ROW LEVEL SECURITY;
ALTER TABLE search_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE enrichment_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE credit_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE credit_balances ENABLE ROW LEVEL SECURITY;
ALTER TABLE payments ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE dashboards ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON contacts
    USING (tenant_id = current_setting('app.current_tenant', true)::text);
CREATE POLICY tenant_isolation ON companies
    USING (tenant_id = current_setting('app.current_tenant', true)::text);
CREATE POLICY tenant_isolation ON search_results
    USING (tenant_id = current_setting('app.current_tenant', true)::text);
CREATE POLICY tenant_isolation ON enrichment_log
    USING (tenant_id = current_setting('app.current_tenant', true)::text);
CREATE POLICY tenant_isolation ON credit_ledger
    USING (tenant_id = current_setting('app.current_tenant', true)::text);
CREATE POLICY tenant_isolation ON credit_balances
    USING (tenant_id = current_setting('app.current_tenant', true)::text);
CREATE POLICY tenant_isolation ON payments
    USING (tenant_id = current_setting('app.current_tenant', true)::text);
CREATE POLICY tenant_isolation ON tenant_keys
    USING (tenant_id = current_setting('app.current_tenant', true)::text);
CREATE POLICY tenant_isolation ON dashboards
    USING (tenant_id = current_setting('app.current_tenant', true)::text);

ALTER TABLE contacts FORCE ROW LEVEL SECURITY;
ALTER TABLE companies FORCE ROW LEVEL SECURITY;
ALTER TABLE search_results FORCE ROW LEVEL SECURITY;
ALTER TABLE enrichment_log FORCE ROW LEVEL SECURITY;
ALTER TABLE credit_ledger FORCE ROW LEVEL SECURITY;
ALTER TABLE credit_balances FORCE ROW LEVEL SECURITY;
ALTER TABLE payments FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_keys FORCE ROW LEVEL SECURITY;
ALTER TABLE dashboards FORCE ROW LEVEL SECURITY;

-- APPLICATION ROLE (matching staging: nrv_api)

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nrv_api') THEN
        CREATE ROLE nrv_api LOGIN PASSWORD 'CHANGE_ME_BEFORE_RUNNING';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE nrv TO nrv_api;
GRANT USAGE ON SCHEMA public TO nrv_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO nrv_api;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO nrv_api;

ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO nrv_api;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO nrv_api;


-- ============================================================
-- 002: Domain Index
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_tenants_domain ON tenants(domain)
    WHERE domain IS NOT NULL;


-- ============================================================
-- 003: Run Steps
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

CREATE INDEX idx_run_steps_tenant_workflow ON run_steps(tenant_id, workflow_id, created_at);
CREATE INDEX idx_run_steps_tenant_created ON run_steps(tenant_id, created_at DESC);
CREATE INDEX idx_run_steps_tool ON run_steps(tenant_id, tool_name);

ALTER TABLE run_steps ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON run_steps
    USING (tenant_id = current_setting('app.current_tenant', true)::text);
ALTER TABLE run_steps FORCE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE ON run_steps TO nrv_api;


-- ============================================================
-- 004: Workflow Label
-- ============================================================

ALTER TABLE run_steps ADD COLUMN IF NOT EXISTS workflow_label TEXT;

CREATE INDEX IF NOT EXISTS idx_run_steps_workflow_id ON run_steps (tenant_id, workflow_id);


-- ============================================================
-- 005: Datasets
-- ============================================================

CREATE TABLE datasets (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    slug            TEXT NOT NULL,
    description     TEXT,
    columns         JSONB NOT NULL DEFAULT '[]',
    dedup_key       TEXT,
    row_count       INTEGER DEFAULT 0,
    created_by_workflow TEXT,
    status          TEXT DEFAULT 'active' CHECK (status IN ('active', 'archived', 'deleted')),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, slug)
);

CREATE TABLE dataset_rows (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    dataset_id      UUID NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    data            JSONB NOT NULL DEFAULT '{}',
    dedup_hash      TEXT,
    workflow_id     TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_datasets_tenant ON datasets(tenant_id);
CREATE INDEX idx_datasets_slug ON datasets(tenant_id, slug);

CREATE INDEX idx_dataset_rows_dataset ON dataset_rows(dataset_id);
CREATE INDEX idx_dataset_rows_tenant ON dataset_rows(tenant_id);
CREATE INDEX idx_dataset_rows_dedup ON dataset_rows(dataset_id, dedup_hash)
    WHERE dedup_hash IS NOT NULL;
CREATE INDEX idx_dataset_rows_created ON dataset_rows(dataset_id, created_at DESC);
CREATE INDEX idx_dataset_rows_data ON dataset_rows USING GIN (data);

ALTER TABLE datasets ENABLE ROW LEVEL SECURITY;
ALTER TABLE dataset_rows ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON datasets
    USING (tenant_id = current_setting('app.current_tenant', true)::text);
CREATE POLICY tenant_isolation ON dataset_rows
    USING (tenant_id = current_setting('app.current_tenant', true)::text);

ALTER TABLE datasets FORCE ROW LEVEL SECURITY;
ALTER TABLE dataset_rows FORCE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE ON datasets TO nrv_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON dataset_rows TO nrv_api;


-- ============================================================
-- 006: Scheduled Workflows
-- ============================================================

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

GRANT SELECT, INSERT, UPDATE, DELETE ON scheduled_workflows TO nrv_api;


-- ============================================================
-- 007: Dashboard Datasets + Feedback
-- ============================================================

ALTER TABLE dashboards ADD COLUMN IF NOT EXISTS dataset_id UUID REFERENCES datasets(id) ON DELETE SET NULL;
ALTER TABLE dashboards ADD COLUMN IF NOT EXISTS config JSONB NOT NULL DEFAULT '{}';
ALTER TABLE dashboards ALTER COLUMN s3_path DROP NOT NULL;

CREATE INDEX IF NOT EXISTS idx_dashboards_dataset ON dashboards(dataset_id);
CREATE INDEX IF NOT EXISTS idx_dashboards_read_token ON dashboards(read_token_hash);

CREATE TABLE IF NOT EXISTS feedback (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id   TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id     TEXT,
    type        TEXT NOT NULL DEFAULT 'feedback' CHECK (type IN ('feedback', 'bug', 'feature')),
    message     TEXT NOT NULL,
    context     JSONB,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_feedback_tenant ON feedback(tenant_id);
CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at DESC);

ALTER TABLE feedback ENABLE ROW LEVEL SECURITY;
ALTER TABLE feedback FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON feedback
    USING (tenant_id = current_setting('app.current_tenant', true)::text);

GRANT SELECT, INSERT, UPDATE, DELETE ON feedback TO nrv_api;

ALTER TABLE dashboards ADD COLUMN IF NOT EXISTS read_token TEXT;


-- ============================================================
-- 008: Hosted Apps
-- ============================================================

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

ALTER TABLE hosted_apps ENABLE ROW LEVEL SECURITY;
CREATE POLICY hosted_apps_tenant_isolation ON hosted_apps
    USING (tenant_id = current_setting('app.current_tenant', true));

GRANT SELECT, INSERT, UPDATE, DELETE ON hosted_apps TO nrv_api;


-- ============================================================
-- 015: Rekey refresh_tokens on Supabase subject_id
-- ============================================================

DROP TABLE IF EXISTS refresh_tokens;

CREATE TABLE refresh_tokens (
    id              SERIAL PRIMARY KEY,
    subject_id      TEXT NOT NULL,
    tenant_id       TEXT NOT NULL,
    email           TEXT,
    channel         TEXT NOT NULL DEFAULT 'cli',
    token_hash      TEXT NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_refresh_tokens_subject ON refresh_tokens(subject_id);
CREATE INDEX idx_refresh_tokens_hash ON refresh_tokens(token_hash);


-- ============================================================
-- Done. Verify with: SELECT * FROM schema_migrations ORDER BY version;
-- ============================================================
