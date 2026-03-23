-- Migration 005: Persistent datasets for workflow data accumulation
-- Datasets are tenant-scoped tables that workflows can create and write to.
-- They serve as the data layer for user-built dashboards.

-- ============================================================
-- DATASETS (metadata about each user-created dataset)
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

-- ============================================================
-- DATASET ROWS (actual data stored as JSONB documents)
-- ============================================================

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

-- ============================================================
-- INDEXES
-- ============================================================

CREATE INDEX idx_datasets_tenant ON datasets(tenant_id);
CREATE INDEX idx_datasets_slug ON datasets(tenant_id, slug);

CREATE INDEX idx_dataset_rows_dataset ON dataset_rows(dataset_id);
CREATE INDEX idx_dataset_rows_tenant ON dataset_rows(tenant_id);
CREATE INDEX idx_dataset_rows_dedup ON dataset_rows(dataset_id, dedup_hash)
    WHERE dedup_hash IS NOT NULL;
CREATE INDEX idx_dataset_rows_created ON dataset_rows(dataset_id, created_at DESC);

-- GIN index for fast JSONB queries on row data
CREATE INDEX idx_dataset_rows_data ON dataset_rows USING GIN (data);

-- ============================================================
-- ROW LEVEL SECURITY
-- ============================================================

ALTER TABLE datasets ENABLE ROW LEVEL SECURITY;
ALTER TABLE dataset_rows ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON datasets
    USING (tenant_id = current_setting('app.current_tenant', true)::text);
CREATE POLICY tenant_isolation ON dataset_rows
    USING (tenant_id = current_setting('app.current_tenant', true)::text);

ALTER TABLE datasets FORCE ROW LEVEL SECURITY;
ALTER TABLE dataset_rows FORCE ROW LEVEL SECURITY;

-- Grant to API role
GRANT SELECT, INSERT, UPDATE, DELETE ON datasets TO nrev_lite_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON dataset_rows TO nrev_lite_api;
