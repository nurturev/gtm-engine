User (in Claude Code)
    → Claude reads .claude/skills/ (GTM knowledge — what to do)
    → Claude calls MCP tools (how to do it)
        → MCP Server (stdio JSON-RPC process)
            → nrev-lite API server (HTTP + JWT)
                → External providers (Apollo, Google, etc.)

  Yes, nrev-lite creates an MCP server

  The MCP server lives at src/nrev_lite/mcp/server.py (~2200 lines). It runs as a stdio process — Claude Code launches it, sends JSON-RPC messages over stdin, reads responses from stdout.

  Purpose: Expose 33 tools (search, enrich, scrape, datasets, apps, scripts, etc.) that Claude can call. Every tool handler forwards the request to your nrev-lite API server over HTTP with JWT auth + workflow tracking headers.

  How it gets registered

  nrev-lite init (src/nrev_lite/cli/init.py) does 3 things:
  1. Authenticates via Google OAuth
  2. Registers the MCP server in .mcp.json (project) or ~/.claude/settings.json (global):
  {
    "mcpServers": {
      "nrev-lite": {
        "command": "nrev-lite",
        "args": ["mcp", "serve"]
      }
    }
  }
  3. Verifies the API server is reachable

  After this, every new Claude Code session auto-starts the MCP server and all 33 tools appear.

  MCP Tools vs Skills — two separate things

|                | Skills (`.claude/skills/`)                                                              | MCP Tools (`mcp/server.py`)                        |
|----------------|-----------------------------------------------------------------------------------------|----------------------------------------------------|
| **What**       | Markdown docs loaded into Claude's context                                              | JSON-RPC tools Claude can call                     |
| **Purpose**    | GTM intelligence — what to do                                                           | API surface — how to do it                         |
| **Examples**   | "Use Apollo for phone numbers", "Always call search_patterns before Google search"      | `nrev_enrich_person`, `nrev_google_search`         |
| **Installed by** | `nrev-lite setup-claude`                                                             | `nrev-lite init`                                   |

  Skills guide Claude's decision-making. MCP tools are the actual executable actions. Both are needed — skills without tools = knowledge but no hands; tools without skills = hands but no strategy.