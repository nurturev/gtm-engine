-- 017_cascade_scripts_learning_logs.sql
-- Add ON DELETE CASCADE to scripts.tenant_id and learning_logs.tenant_id so
-- tenant cleanups become single-statement. Matches the cascade posture of
-- every other tenant-scoped table in this schema.

BEGIN;

-- scripts.tenant_id
ALTER TABLE scripts
    DROP CONSTRAINT IF EXISTS scripts_tenant_id_fkey;
ALTER TABLE scripts
    ADD CONSTRAINT scripts_tenant_id_fkey
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;

-- learning_logs.tenant_id
ALTER TABLE learning_logs
    DROP CONSTRAINT IF EXISTS learning_logs_tenant_id_fkey;
ALTER TABLE learning_logs
    ADD CONSTRAINT learning_logs_tenant_id_fkey
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;

COMMIT;
