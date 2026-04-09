-- 015: Rekey refresh_tokens on subject_id (Supabase user UUID).
--
-- Context: After the CLI auth migration, gtm-engine no longer owns user
-- identity — Supabase does. The exchange endpoint receives a Supabase JWT,
-- extracts its `sub` claim (a Supabase user UUID), and issues a gtm-engine
-- access + refresh token pair. Refresh tokens must therefore be keyed on the
-- Supabase UUID rather than on a local users.id row.
--
-- Per spec §16 #12, gtm-engine is not yet in production, so historical refresh
-- token rows do not need to be preserved. Drop and recreate is safe.
--
-- The new columns tenant_id / email / channel are stored on the row so that
-- on rotation we can mint a fresh access token without consulting Supabase or
-- any local users/tenants table — keeping the refresh path zero-dependency on
-- external services (per spec §13).

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
