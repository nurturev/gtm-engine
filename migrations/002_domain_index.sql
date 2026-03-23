-- ============================================================
-- nrev-lite Database Schema — Domain-based Tenant Lookup
-- Version: 002
-- Date: 2026-03-16
-- ============================================================

-- Index on tenants.domain for fast business-domain lookups
-- Used by find_or_create_user() to join users to existing tenants
CREATE INDEX IF NOT EXISTS idx_tenants_domain ON tenants(domain)
    WHERE domain IS NOT NULL;
