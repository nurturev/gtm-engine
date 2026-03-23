-- ============================================================
-- nrev-lite Database Schema — Initial Migration
-- Version: 001
-- Date: 2026-03-15
-- ============================================================

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================
-- TENANTS & USERS
-- ============================================================

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

CREATE TABLE refresh_tokens (
    id              SERIAL PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash      TEXT NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_refresh_tokens_user ON refresh_tokens(user_id);
CREATE INDEX idx_refresh_tokens_hash ON refresh_tokens(token_hash);

-- ============================================================
-- INTERACTIVE TABLES (tenant data)
-- ============================================================

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

-- ============================================================
-- ENRICHMENT LOG (audit trail)
-- ============================================================

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

-- ============================================================
-- CREDIT SYSTEM
-- ============================================================

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

-- ============================================================
-- PAYMENTS
-- ============================================================

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

-- ============================================================
-- TENANT KEYS (BYOK)
-- ============================================================

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

-- ============================================================
-- DASHBOARDS
-- ============================================================

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

-- ============================================================
-- INDEXES
-- ============================================================

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

-- ============================================================
-- ROW LEVEL SECURITY
-- ============================================================

ALTER TABLE contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE companies ENABLE ROW LEVEL SECURITY;
ALTER TABLE search_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE enrichment_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE credit_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE credit_balances ENABLE ROW LEVEL SECURITY;
ALTER TABLE payments ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE dashboards ENABLE ROW LEVEL SECURITY;

-- RLS Policies: tenant can only access their own data
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

-- Force RLS even for table owners (important for security)
ALTER TABLE contacts FORCE ROW LEVEL SECURITY;
ALTER TABLE companies FORCE ROW LEVEL SECURITY;
ALTER TABLE search_results FORCE ROW LEVEL SECURITY;
ALTER TABLE enrichment_log FORCE ROW LEVEL SECURITY;
ALTER TABLE credit_ledger FORCE ROW LEVEL SECURITY;
ALTER TABLE credit_balances FORCE ROW LEVEL SECURITY;
ALTER TABLE payments FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_keys FORCE ROW LEVEL SECURITY;
ALTER TABLE dashboards FORCE ROW LEVEL SECURITY;

-- ============================================================
-- Create an application role for the API server
-- (The API server connects as this role, RLS applies)
-- ============================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nrev_lite_api') THEN
        CREATE ROLE nrev_lite_api LOGIN PASSWORD 'nrev_lite_api_local_dev';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE nrev_lite TO nrev_lite_api;
GRANT USAGE ON SCHEMA public TO nrev_lite_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO nrev_lite_api;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO nrev_lite_api;

-- Ensure future tables also get grants
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO nrev_lite_api;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO nrev_lite_api;

-- ============================================================
-- Done
-- ============================================================
