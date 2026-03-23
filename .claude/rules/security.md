# Security Rules for nrev-lite Development

When working with API keys or secrets:
- Never log or print API key values
- Never return key values from any function
- Always use fingerprints (last 4 chars) for identification
- BYOK keys are encrypted at rest via server/vault/ (Fernet in dev, KMS in prod)
- Platform keys live in environment variables, never in code or database
- Never include keys in commit messages or comments

When handling user-provided keys:
- Keys are stored via the /api/v1/keys endpoint (encrypted server-side)
- Never hold decrypted keys in memory longer than needed
- Remind users that keys shown in chat context should be rotated

When working with tenant data:
- Never bypass Row-Level Security (RLS) — always call set_tenant_context() before queries
- Never store plaintext keys in the database
- JWT tokens: 24h access, 30d refresh
- All BYOK keys encrypted with KMS encryption context including tenant_id

When running tests:
- Run: pytest tests/
