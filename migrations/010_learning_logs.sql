-- 010_learning_logs.sql — Self-learning system: capture workflow discoveries
-- Depends on: 001_tenants.sql

BEGIN;

-- Learning logs — every discovery Claude makes during workflows
CREATE TABLE IF NOT EXISTS learning_logs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           TEXT NOT NULL REFERENCES tenants(id),
    category            TEXT NOT NULL,
    subcategory         TEXT,
    platform            TEXT,
    tool_name           TEXT,
    discovery           JSONB NOT NULL,
    evidence            JSONB NOT NULL DEFAULT '[]',
    source_workflow_id  TEXT,
    confidence          REAL NOT NULL DEFAULT 0.5,
    status              TEXT NOT NULL DEFAULT 'pending',
    reviewed_by         TEXT,
    reviewed_at         TIMESTAMPTZ,
    merged_at           TIMESTAMPTZ,
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes for admin queries
CREATE INDEX IF NOT EXISTS idx_learning_logs_status ON learning_logs(status);
CREATE INDEX IF NOT EXISTS idx_learning_logs_category ON learning_logs(category);
CREATE INDEX IF NOT EXISTS idx_learning_logs_created ON learning_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_learning_logs_tenant ON learning_logs(tenant_id);

-- Dynamic knowledge — approved learnings available to all users at runtime
CREATE TABLE IF NOT EXISTS dynamic_knowledge (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category            TEXT NOT NULL,
    key                 TEXT NOT NULL,
    knowledge           JSONB NOT NULL,
    source_learning_id  UUID REFERENCES learning_logs(id),
    enabled             BOOLEAN NOT NULL DEFAULT true,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(category, key)
);

CREATE INDEX IF NOT EXISTS idx_dynamic_knowledge_lookup ON dynamic_knowledge(category, key) WHERE enabled = true;

-- No RLS on these tables — learning_logs are written by tenants but read by admins globally
-- dynamic_knowledge is read by all users (public knowledge base)
-- Write access on learning_logs is controlled at the API layer (tenant auth for POST)

GRANT SELECT, INSERT ON learning_logs TO nrev_lite_api;
GRANT SELECT, INSERT, UPDATE ON dynamic_knowledge TO nrev_lite_api;
-- Admin operations (approve/reject/merge) need UPDATE on learning_logs
GRANT UPDATE ON learning_logs TO nrev_lite_api;

COMMIT;
