-- 014: Add label column to tenant_keys for user-friendly key identification
-- e.g. "Production workspace", "Nikhil's Instantly account"

ALTER TABLE tenant_keys ADD COLUMN IF NOT EXISTS label TEXT;
