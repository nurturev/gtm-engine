-- prod_combined_018_019.sql
-- Production replay of migrations 018 + 019.
--   018: Fresh LinkedIn operation_costs seed (8 rows).
--   019: RocketReach Universal operation_costs seed (2 rows — enrich_company,
--        search_companies). enrich_person and search_people already seeded
--        via prod_combined_001_016.sql.
-- All operations priced at 3.0 credits per platform-key call; BYOK is free.
-- Idempotent via ON CONFLICT DO NOTHING on both operation_costs and
-- schema_migrations.

BEGIN;

INSERT INTO operation_costs (vendor, operation, base_cost, description) VALUES
    ('fresh_linkedin', 'enrich_person',        3.0, 'Fresh LinkedIn person profile by LinkedIn URL'),
    ('fresh_linkedin', 'enrich_company',       3.0, 'Fresh LinkedIn company profile by URL or domain'),
    ('fresh_linkedin', 'fetch_profile_posts',  3.0, 'Fresh LinkedIn profile posts by LinkedIn URL'),
    ('fresh_linkedin', 'fetch_company_posts',  3.0, 'Fresh LinkedIn company posts by LinkedIn URL'),
    ('fresh_linkedin', 'fetch_post_details',   3.0, 'Fresh LinkedIn single post detail by URN'),
    ('fresh_linkedin', 'fetch_post_reactions', 3.0, 'Fresh LinkedIn reactions on a post by URN'),
    ('fresh_linkedin', 'fetch_post_comments',  3.0, 'Fresh LinkedIn comments on a post by URN'),
    ('fresh_linkedin', 'search_posts',         3.0, 'Fresh LinkedIn filter-driven post search'),
    ('rocketreach',    'enrich_company',       3.0, 'RocketReach company enrichment (Universal)'),
    ('rocketreach',    'search_companies',     3.0, 'RocketReach company search (Universal)')
ON CONFLICT (vendor, operation) DO NOTHING;

INSERT INTO schema_migrations (version, filename) VALUES
    ('018', '018_fresh_linkedin_operation_costs.sql'),
    ('019', '019_rocketreach_universal_operation_costs.sql')
ON CONFLICT (version) DO NOTHING;

COMMIT;
