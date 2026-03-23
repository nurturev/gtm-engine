-- Migration 007: Link dashboards to datasets + feedback table
-- Run: psql -U nrev_lite -d nrev_lite -f migrations/007_dashboard_datasets.sql

BEGIN;

-- ============================================================
-- 1. Extend dashboards table for dataset-backed rendering
-- ============================================================

ALTER TABLE dashboards ADD COLUMN IF NOT EXISTS dataset_id UUID REFERENCES datasets(id) ON DELETE SET NULL;
ALTER TABLE dashboards ADD COLUMN IF NOT EXISTS config JSONB NOT NULL DEFAULT '{}';
ALTER TABLE dashboards ALTER COLUMN s3_path DROP NOT NULL;

CREATE INDEX IF NOT EXISTS idx_dashboards_dataset ON dashboards(dataset_id);
CREATE INDEX IF NOT EXISTS idx_dashboards_read_token ON dashboards(read_token_hash);

-- ============================================================
-- 2. Feedback table
-- ============================================================

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

-- RLS
ALTER TABLE feedback ENABLE ROW LEVEL SECURITY;
ALTER TABLE feedback FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON feedback
    USING (tenant_id = current_setting('app.current_tenant', true)::text);

-- Grants
GRANT SELECT, INSERT, UPDATE, DELETE ON feedback TO nrev_lite_api;

COMMIT;

-- Store raw read_token for share link retrieval
ALTER TABLE dashboards ADD COLUMN IF NOT EXISTS read_token TEXT;
