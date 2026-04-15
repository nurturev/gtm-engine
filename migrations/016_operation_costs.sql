-- 016: Configurable operation credit costs
--
-- Moves per-operation credit costs from hardcoded Python dicts into the
-- database so admins can add/edit/delete costs at runtime without a deploy.
--
-- This is a global (non-tenant) table — no RLS. Admin-only write access
-- is enforced at the API layer.

BEGIN;

CREATE TABLE IF NOT EXISTS operation_costs (
    id          SERIAL PRIMARY KEY,
    vendor      TEXT NOT NULL,
    operation   TEXT NOT NULL,
    base_cost   NUMERIC(10,2) NOT NULL DEFAULT 1.0,
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(vendor, operation)
);

CREATE INDEX IF NOT EXISTS idx_operation_costs_lookup
    ON operation_costs(vendor, operation);

GRANT SELECT, INSERT, UPDATE, DELETE ON operation_costs TO nrv_api;
GRANT USAGE, SELECT ON SEQUENCE operation_costs_id_seq TO nrv_api;

-- Seed from current hardcoded values
INSERT INTO operation_costs (vendor, operation, base_cost, description) VALUES
    -- Apollo
    ('apollo', 'search_people',          3.0, 'Apollo people search'),
    ('apollo', 'enrich_person',          3.0, 'Apollo person enrichment'),
    ('apollo', 'enrich_company',         3.0, 'Apollo company enrichment'),
    ('apollo', 'search_companies',       3.0, 'Apollo company search'),
    ('apollo', 'bulk_enrich_people',     3.0, 'Apollo bulk people enrichment (per record)'),
    ('apollo', 'bulk_enrich_companies',  3.0, 'Apollo bulk company enrichment (per record)'),
    -- RocketReach
    ('rocketreach', 'search_people',     3.0, 'RocketReach people search'),
    ('rocketreach', 'enrich_person',     3.0, 'RocketReach person enrichment'),
    -- RapidAPI (Google Search)
    ('rapidapi', 'search_web',           3.0, 'Google search via RapidAPI'),
    ('rapidapi', 'google_search',        3.0, 'Google SERP via RapidAPI'),
    -- Parallel Web (Scraping)
    ('parallel', 'scrape_page',          3.0, 'Parallel web scrape'),
    ('parallel', 'crawl_site',           3.0, 'Parallel web crawl (base cost)'),
    ('parallel', 'extract_structured',   3.0, 'Parallel structured extraction'),
    ('parallel', 'batch_extract',        3.0, 'Parallel batch extraction (per item)'),
    -- PredictLeads (Signals)
    ('predictleads', 'company_jobs',          3.0, 'PredictLeads job signals'),
    ('predictleads', 'company_technologies',  3.0, 'PredictLeads tech stack'),
    ('predictleads', 'company_news',          3.0, 'PredictLeads news'),
    ('predictleads', 'company_financing',     3.0, 'PredictLeads financing'),
    ('predictleads', 'similar_companies',     3.0, 'PredictLeads similar companies')
ON CONFLICT (vendor, operation) DO NOTHING;

COMMIT;
