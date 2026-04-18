-- 018_fresh_linkedin_operation_costs.sql
-- Seed operation_costs rows for every Fresh LinkedIn operation (Phase 1 + Phase 2).
-- All operations cost 3 credits per platform-key call; BYOK is free.

BEGIN;

INSERT INTO operation_costs (vendor, operation, base_cost, description) VALUES
    ('fresh_linkedin', 'enrich_person',        3.0, 'Fresh LinkedIn person profile by LinkedIn URL'),
    ('fresh_linkedin', 'enrich_company',       3.0, 'Fresh LinkedIn company profile by URL or domain'),
    ('fresh_linkedin', 'fetch_profile_posts',  3.0, 'Fresh LinkedIn profile posts by LinkedIn URL'),
    ('fresh_linkedin', 'fetch_company_posts',  3.0, 'Fresh LinkedIn company posts by LinkedIn URL'),
    ('fresh_linkedin', 'fetch_post_details',   3.0, 'Fresh LinkedIn single post detail by URN'),
    ('fresh_linkedin', 'fetch_post_reactions', 3.0, 'Fresh LinkedIn reactions on a post by URN'),
    ('fresh_linkedin', 'fetch_post_comments',  3.0, 'Fresh LinkedIn comments on a post by URN'),
    ('fresh_linkedin', 'search_posts',         3.0, 'Fresh LinkedIn filter-driven post search')
ON CONFLICT (vendor, operation) DO NOTHING;

INSERT INTO schema_migrations (version, filename) VALUES
    ('018', '018_fresh_linkedin_operation_costs.sql')
ON CONFLICT (version) DO NOTHING;

COMMIT;
