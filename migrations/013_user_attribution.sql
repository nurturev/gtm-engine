-- nrev-lite Database Schema — User attribution + multi-account connections
-- Run: psql -U nrev_lite -d nrev_lite -f migrations/013_user_attribution.sql

-- run_steps: who ran each workflow step
ALTER TABLE run_steps ADD COLUMN IF NOT EXISTS user_id TEXT;
CREATE INDEX IF NOT EXISTS idx_run_steps_user
    ON run_steps (tenant_id, user_id) WHERE user_id IS NOT NULL;

-- credit_ledger: who triggered each charge
ALTER TABLE credit_ledger ADD COLUMN IF NOT EXISTS user_id TEXT;
CREATE INDEX IF NOT EXISTS idx_credit_ledger_user
    ON credit_ledger (tenant_id, user_id) WHERE user_id IS NOT NULL;

-- Track which user connected which Composio account
CREATE TABLE IF NOT EXISTS user_connections (
    id                  SERIAL PRIMARY KEY,
    tenant_id           TEXT NOT NULL,
    user_id             TEXT NOT NULL REFERENCES users(id),
    user_email          TEXT NOT NULL,
    app_id              TEXT NOT NULL,
    composio_entity_id  TEXT NOT NULL,
    composio_account_id TEXT,
    status              TEXT NOT NULL DEFAULT 'active',
    connected_at        TIMESTAMPTZ DEFAULT now(),
    UNIQUE(tenant_id, user_id, app_id)
);

ALTER TABLE user_connections ENABLE ROW LEVEL SECURITY;

CREATE POLICY user_connections_tenant ON user_connections
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

GRANT ALL ON user_connections TO nrev_api;
GRANT USAGE, SELECT ON SEQUENCE user_connections_id_seq TO nrev_api;
