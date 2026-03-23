# nrev-lite — Agent-Native GTM Execution Platform

AI-native go-to-market platform. Makes Claude Code your GTM co-pilot with multi-provider enrichment, OAuth app connections, credit billing, and workflow tracking.

## Quick Start

```bash
# Install the CLI
pip install nrev-lite

# Authenticate with Google
nrev-lite auth login

# Set up Claude Code integration
nrev-lite setup claude

# Restart Claude Code — done.
```

See [QUICKSTART.md](QUICKSTART.md) for detailed setup and [HANDOVER.md](HANDOVER.md) for deployment instructions.

## Architecture

```
src/nrev_lite/         → CLI + MCP server (published to PyPI, installed by users)
server/          → FastAPI API server (deployed to cloud)
migrations/      → PostgreSQL schema + RLS migrations
```

**Split design:** The CLI is a thin client that authenticates and talks to the server. The server handles provider routing, credit billing, key encryption, and data persistence.

## CLI Commands

| Command | What It Does |
|---------|-------------|
| `nrev-lite auth login` | Authenticate with Google OAuth |
| `nrev-lite enrich person` | Person enrichment (email, name, LinkedIn) |
| `nrev-lite enrich company` | Company enrichment (domain, name) |
| `nrev-lite search people` | Search for people with filters |
| `nrev-lite tables list` | List available data tables |
| `nrev-lite keys list` | List BYOK API keys |
| `nrev-lite credits` | Check credit balance |
| `nrev-lite status` | Auth, server, and credit status |
| `nrev-lite setup claude` | Auto-configure Claude Code MCP integration |
| `nrev-lite dashboard` | Open the tenant dashboard |

## MCP Tools (15 tools for Claude Code)

| Tool | Purpose |
|------|---------|
| `nrev_health` | Health check |
| `nrev_google_search` | Advanced Google SERP with operators and bulk queries |
| `nrev_search_patterns` | Platform-specific search query patterns |
| `nrev_enrich_person` | Person enrichment |
| `nrev_enrich_company` | Company enrichment |
| `nrev_query_table` | Query stored data |
| `nrev_list_tables` | List available tables |
| `nrev_credit_balance` | Check credits |
| `nrev_provider_status` | Provider availability |
| `nrev_list_connections` | OAuth-connected apps |
| `nrev_execute_action` | Execute actions on connected apps |

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
