# nrv — Agent-Native GTM Execution Platform

AI-native go-to-market platform. Makes Claude Code your GTM co-pilot with multi-provider enrichment, OAuth app connections, credit billing, and workflow tracking.

## Quick Start

```bash
# Install the CLI
pip install nrv

# Authenticate with Google
nrv auth login

# Set up Claude Code integration
nrv setup claude

# Restart Claude Code — done.
```

See [QUICKSTART.md](QUICKSTART.md) for detailed setup and [HANDOVER.md](HANDOVER.md) for deployment instructions.

## Architecture

```
src/nrv/         → CLI + MCP server (published to PyPI, installed by users)
server/          → FastAPI API server (deployed to cloud)
migrations/      → PostgreSQL schema + RLS migrations
```

**Split design:** The CLI is a thin client that authenticates and talks to the server. The server handles provider routing, credit billing, key encryption, and data persistence.

## CLI Commands

| Command | What It Does |
|---------|-------------|
| `nrv auth login` | Authenticate with Google OAuth |
| `nrv enrich person` | Person enrichment (email, name, LinkedIn) |
| `nrv enrich company` | Company enrichment (domain, name) |
| `nrv search people` | Search for people with filters |
| `nrv tables list` | List available data tables |
| `nrv keys list` | List BYOK API keys |
| `nrv credits` | Check credit balance |
| `nrv status` | Auth, server, and credit status |
| `nrv setup claude` | Auto-configure Claude Code MCP integration |
| `nrv dashboard` | Open the tenant dashboard |

## MCP Tools (15 tools for Claude Code)

| Tool | Purpose |
|------|---------|
| `nrv_health` | Health check |
| `nrv_google_search` | Advanced Google SERP with operators and bulk queries |
| `nrv_search_patterns` | Platform-specific search query patterns |
| `nrv_enrich_person` | Person enrichment |
| `nrv_enrich_company` | Company enrichment |
| `nrv_query_table` | Query stored data |
| `nrv_list_tables` | List available tables |
| `nrv_credit_balance` | Check credits |
| `nrv_provider_status` | Provider availability |
| `nrv_list_connections` | OAuth-connected apps |
| `nrv_execute_action` | Execute actions on connected apps |

## Supported Providers

Apollo, RocketReach, RapidAPI Google, Parallel AI, PredictLeads, Composio (OAuth connections), and more.

## Security

- Multi-tenant isolation via PostgreSQL Row-Level Security
- BYOK keys encrypted at rest (Fernet in dev, KMS in production)
- Platform API keys stored as environment variables, never exposed
- JWT authentication with short-lived tokens
- `.env` and all secrets are gitignored

## License

MIT — nRev, Inc.
