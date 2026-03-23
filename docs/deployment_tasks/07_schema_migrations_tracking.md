# Task 07: Add Schema Migrations Tracking

**Status:** Not Started
**Priority:** P1 — Important for operational safety but not blocking first deploy
**Deployment Doc Reference:** Section 6

---

## Goal

Create a `schema_migrations` table to track which SQL migrations have been applied, preventing accidental re-application or missed migrations.

---

## What to Create

New file: `migrations/000_schema_migrations.sql`

```sql
-- Migration version tracking table
-- Run this BEFORE applying other migrations on a fresh DB,
-- or AFTER applying all existing migrations on an existing DB.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Record all existing migrations (idempotent)
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
```

---

## Files to Create

| File | Purpose |
|------|---------|
| `migrations/000_schema_migrations.sql` | Creates tracking table and seeds existing migration records |

---

## Usage Pattern

For future migrations (e.g., `009_new_feature.sql`):

```bash
# 1. Check if already applied
psql -h <endpoint> -U postgres -d nrev-lite -c \
  "SELECT version FROM schema_migrations WHERE version = '009';"

# 2. Apply migration
psql -h <endpoint> -U postgres -d nrev-lite -f migrations/009_new_feature.sql

# 3. Record it
psql -h <endpoint> -U postgres -d nrev-lite -c \
  "INSERT INTO schema_migrations (version, filename) VALUES ('009', '009_new_feature.sql');"
```

---

## Acceptance Criteria

- [ ] `migrations/000_schema_migrations.sql` exists and is valid SQL
- [ ] Running it on a fresh DB creates the `schema_migrations` table
- [ ] Running it on an existing DB with all 8 migrations records them without error (idempotent)
- [ ] Running it twice is safe (ON CONFLICT DO NOTHING)

---

## Testing

```bash
# Against local Docker PostgreSQL
docker exec -i nrev-lite-postgres psql -U nrev-lite -d nrev-lite < migrations/000_schema_migrations.sql
docker exec -i nrev-lite-postgres psql -U nrev-lite -d nrev-lite -c "SELECT * FROM schema_migrations ORDER BY version;"
# Should show 8 rows (001 through 008)

# Run again — should be idempotent
docker exec -i nrev-lite-postgres psql -U nrev-lite -d nrev-lite < migrations/000_schema_migrations.sql
# No errors
```
