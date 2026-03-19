-- Migration version tracking table.
-- Run this BEFORE applying migrations on a fresh DB,
-- or AFTER applying all existing migrations on an existing DB.
-- Idempotent — safe to run multiple times.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Record all existing migrations (idempotent via ON CONFLICT)
INSERT INTO schema_migrations (version, filename) VALUES
    ('001', '001_initial.sql'),
    ('002', '002_domain_index.sql'),
    ('003', '003_run_steps.sql'),
    ('004', '004_workflow_label.sql'),
    ('005', '005_datasets.sql'),
    ('006', '006_scheduled_workflows.sql'),
    ('007', '007_dashboard_datasets.sql'),
    ('008', '008_hosted_apps.sql')
ON CONFLICT (version) DO NOTHING;
