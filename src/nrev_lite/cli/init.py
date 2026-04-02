"""nrev-lite init — one-command onboarding for new users.

Handles the complete setup flow:
1. Authenticate (Google OAuth via browser)
2. Register the MCP server via `claude mcp add`
3. Install Claude Code skills and CLAUDE.md (so Claude knows WHEN to use nrev-lite)
4. Verify everything works

After `nrev-lite init`, every new Claude Code session automatically has access
to all nrev-lite tools AND Claude knows when/how to use them.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import click

from nrev_lite.client.auth import is_authenticated, load_credentials
from nrev_lite.utils.config import get_api_base_url
from nrev_lite.utils.display import print_error, print_success, print_warning


def _find_nrev_executable() -> str:
    """Find the path to the nrev-lite entry point for MCP server.

    Returns the absolute path to the nrev-lite binary that Claude Code
    should use to start the MCP server. Falls back to python -m.
    """
    nrev_bin = shutil.which("nrev-lite")
    if nrev_bin:
        return nrev_bin

    python_bin = shutil.which("python3") or shutil.which("python") or sys.executable
    return python_bin


def _is_already_registered() -> bool:
    """Check if nrev-lite MCP server is already registered in Claude Code."""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return False

    result = subprocess.run(
        ["claude", "mcp", "list"],
        capture_output=True,
        text=True
    )
    return "nrev-lite" in result.stdout


def _register_mcp_server(scope: str) -> bool:
    """Register nrev-lite as an MCP server via `claude mcp add`.

    Uses the Claude Code CLI to register the server in the correct
    config file (~/.claude.json), which is the only file Claude Code
    reads MCP server definitions from.

    Args:
        scope: "user" for all sessions, "local" for current project only.

    Returns True if registration was successful.
    """
    claude_bin = shutil.which("claude")
    if not claude_bin:
        print_error(
            "Claude Code CLI not found on PATH.\n"
            "  Install it from: https://claude.ai/download\n"
            "  Then run `nrev-lite init` again."
        )
        return False

    nrev_bin = _find_nrev_executable()

    # Build the command for `claude mcp add`
    if nrev_bin.endswith("nrev-lite"):
        cmd = [
            "claude", "mcp", "add",
            "-s", scope,
            "nrev-lite",
            "--",
            nrev_bin, "mcp", "serve"
        ]
    else:
        cmd = [
            "claude", "mcp", "add",
            "-s", scope,
            "nrev-lite",
            "--",
            nrev_bin, "-m", "nrev_lite.mcp.server"
        ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        stderr = result.stderr.strip()
        print_error(f"Failed to register MCP server: {stderr}")
        return False

    return True


def _unregister_mcp_server(scope: str) -> None:
    """Remove existing nrev-lite MCP registration (for re-registration)."""
    subprocess.run(
        ["claude", "mcp", "remove", "-s", scope, "nrev-lite"],
        capture_output=True,
        text=True
    )


def _get_vscode_settings_path() -> Path:
    """Return the platform-appropriate VSCode global settings.json path."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Code" / "User" / "settings.json"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        return Path(appdata) / "Code" / "User" / "settings.json"
    else:
        return Path.home() / ".config" / "Code" / "User" / "settings.json"


def _is_vscode_terminal() -> bool:
    """Detect whether the current terminal is running inside VSCode."""
    if os.environ.get("VSCODE_IPC_HOOK"):
        return True
    if os.environ.get("VSCODE_IPC_HOOK_CLI"):
        return True
    if os.environ.get("TERM_PROGRAM", "").lower() == "vscode":
        return True
    return False


def _read_json_file(path: Path) -> dict:
    """Read a JSON file, returning {} if it doesn't exist or is invalid."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json_file(path: Path, data: dict) -> None:
    """Write a dict to a JSON file, creating parent dirs if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _build_mcp_config() -> dict:
    """Build the MCP server config dict for nrev-lite."""
    nrev_bin = _find_nrev_executable()
    if nrev_bin.endswith("nrev-lite"):
        return {"command": nrev_bin, "args": ["mcp", "serve"]}
    else:
        return {"command": nrev_bin, "args": ["-m", "nrev_lite.mcp.server"]}


def _register_mcp_vscode(settings_path: Path) -> bool:
    """Register nrev-lite as an MCP server in VSCode's global settings.json."""
    settings = _read_json_file(settings_path)

    mcp_section = settings.get("mcp", {})
    servers = mcp_section.get("servers", {})

    if "nrev-lite" in servers:
        click.echo(f"  nrev-lite MCP server already registered in VSCode ({settings_path})")
        return True

    mcp_config = _build_mcp_config()

    if "mcp" not in settings:
        settings["mcp"] = {}
    if "servers" not in settings["mcp"]:
        settings["mcp"]["servers"] = {}

    settings["mcp"]["servers"]["nrev-lite"] = {
        "command": mcp_config["command"],
        "args": mcp_config["args"],
        "type": "stdio",
    }

    _write_json_file(settings_path, settings)
    return True


def _register_mcp_project() -> bool:
    """Write MCP config to .mcp.json in the current working directory."""
    project_path = Path.cwd() / ".mcp.json"
    settings = _read_json_file(project_path)

    mcp_servers = settings.get("mcpServers", {})
    if "nrev-lite" in mcp_servers:
        click.echo(f"  nrev-lite MCP server already registered in {project_path}")
        return True

    mcp_config = _build_mcp_config()

    if "mcpServers" not in settings:
        settings["mcpServers"] = {}

    settings["mcpServers"]["nrev-lite"] = mcp_config

    _write_json_file(project_path, settings)
    return True


def _verify_server_reachable() -> bool:
    """Check if the nrev-lite API server is reachable."""
    import httpx

    base_url = get_api_base_url()
    try:
        resp = httpx.get(f"{base_url}/health", timeout=5)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


def _install_claude_integration(scope_dir: Path, server_url: str, tenant_id: str) -> bool:
    """Install CLAUDE.md so Claude knows WHEN and HOW to use nrev-lite.

    This is the critical piece — without it, Claude has the tools but no
    knowledge of when to invoke them, what the console is, or how credits work.

    Args:
        scope_dir: ~/.claude (global) or .claude (project-level)
        server_url: The nrev-lite server URL (for console links)
        tenant_id: The user's tenant ID (for console links)

    Returns True if installation succeeded.
    """
    console_url = f"{server_url}/console/{tenant_id}"

    # ── The CLAUDE.md content that teaches Claude about nrev-lite ─────
    claude_md_content = f"""\
# nrev-lite — Agent-Native GTM Execution Platform

You have access to nrev-lite, a GTM (Go-To-Market) platform with 36 MCP tools
for prospecting, enrichment, web intelligence, app integrations, and data management.

## When to Use nrev-lite

Activate nrev-lite tools whenever the user's request matches ANY of these categories.
When in doubt, use nrev-lite — it covers the entire GTM workflow.

### Prospecting & Lead Generation
- "Find me [title] at [company type]" / "Build a list of..." / "Search for people..."
- "Who works at [company]?" / "Get contact info for..." / "Find their email/phone"
- "Target accounts in [industry/geo]" / "ICP matching" / "Look-alike companies"
- Alumni search, past employer search, school network mapping
- "Find companies like [company]" / "Similar companies to..."

### Enrichment & Data Quality
- "Enrich this list" / "Get emails and phones" / "Fill in missing data"
- "Verify these emails" / "Check email deliverability" / "Bounce check"
- "What's their LinkedIn?" / "Company size?" / "Tech stack?" / "Revenue?"
- "Find the best email for [person]" / "Direct dial" / "Mobile number"
- Waterfall enrichment across multiple providers

### Market & Company Research
- "Research [company]" / "What's happening at [company]?" / "Company deep dive"
- "Funding news" / "Recent fundraise" / "Series B companies" / "Just raised"
- "Hiring signals" / "Are they growing?" / "Job postings at [company]"
- "Tech stack discovery" / "What tools do they use?" / "Built with"
- "Competitor analysis" / "Who competes with [company]?" / "Competitive landscape"
- "Market sizing" / "TAM analysis" / "Industry trends" / "Market map"

### Web Intelligence & Scraping
- "Search Google for..." / "Find [anything] online"
- "Scrape this page" / "Extract data from [URL]" / "Pull info from this site"
- "LinkedIn posts about [topic]" / "Twitter/X mentions of [brand]"
- "Reddit discussions about [topic]" / "G2 reviews of [product]"
- "Monitor [topic/company/person]" / "Track job changes"
- "What are people saying about [product/company]?" (social listening)

### Signal-Based Selling & Intent
- "Who's hiring for [role]?" — buying signal for tools that role uses
- "Companies using [competitor product]" — displacement opportunity
- "Recent leadership changes at [company]" — new exec = new budget
- "Companies that just raised funding" / "New office openings"
- "Job postings mentioning [your product category]"
- "Reddit/Twitter posts asking about [your category]" — active buyers
- "Trigger events" / "Buying signals" / "Intent signals"

### Outreach & Campaigns
- "Set up a cold email campaign" / "Add leads to Instantly"
- "Write a cold email to..." / "Personalize outreach for..."
- "Humanize this" / "Remove AI tone" / "Make it sound natural"
- "Email sequence" / "Follow-up cadence" / "Drip campaign"
- "LinkedIn message" / "Connection request message"

### Connected Apps & Integrations
- "Send an email" / "Check my calendar" / "Create a CRM contact"
- "Push leads to HubSpot/Salesforce" / "Update my CRM"
- "Post to Slack" / "Create a task in Linear/ClickUp"
- "What apps can I connect?" / "Connect my [app]" / "Show integrations"
- Use `nrev_app_catalog` to browse all 22 available apps
- Use `nrev_app_list` to see which are already connected

### Data Management & Persistence
- "Save these results" / "Create a dataset" / "Track this over time"
- "Query my saved data" / "Show my datasets" / "Export to Sheets"
- "Build a dashboard" / "Share these results" / "Deploy a site"
- "Schedule this to run daily/weekly" / "Automate this workflow"

### Account-Based Marketing (ABM)
- "ABM workflow for [account]" / "Account plan for [company]"
- "Map the buying committee at [company]" / "Org chart for [company]"
- "Multi-threaded approach" / "Champion tracking" / "Find the decision maker"
- "Account intelligence on [company]" / "Account deep dive"

### GTM Strategy (Consultant Mode)
- "What should I do?" / "Where do I start?" / "Help me figure out..."
- "My reply rates are low" / "Pipeline is slow" / "Bad lead quality"
- "Define my ICP" / "Who should I target?" / "Positioning advice"
- "Go-to-market strategy" / "Channel strategy" / "Pricing strategy"

## ⚠ CRITICAL: Exporting Data to External Apps

When the user wants to send/export/push data to Google Sheets, HubSpot, Salesforce, Gmail,
Slack, or ANY external tool — **use the nrev_app_* MCP tools, NOT bash or CLI commands.**

**Correct flow:**
1. `nrev_app_list` — check if the app is connected
2. `nrev_app_actions(app_id="google_sheets")` — discover available actions
3. `nrev_app_action_schema(action_name="GOOGLESHEETS_BATCH_UPDATE")` — get exact params
4. `nrev_app_execute(app_id, action, params)` — execute the action

**NEVER do any of these:**
- Run bash commands to interact with apps
- Use the CLI (`nrev-lite datasets export`) to push to external apps
- Make raw HTTP calls to the nrev-lite API
- Grep through source code to find endpoints
- Use Python scripts with urllib/httpx to call APIs directly

The CLI `datasets export` command exports to CSV/JSON files locally — it does NOT push to Google
Sheets or any app. To push data to apps, ALWAYS use `nrev_app_execute`.

**nrev-lite datasets ≠ external apps.** Datasets are nrev-lite's internal storage (like a database).
Apps are external tools the user connects (Gmail, Sheets, CRM). Different systems, different tools.

## Console — The User's Dashboard

The nrev-lite console is the user's web dashboard at:
**{console_url}**

Direct the user to the console when they need to:
- **See credit balance & top up**: {console_url}?tab=usage
- **Manage API keys (BYOK)**: {console_url}?tab=keys
- **Connect or manage apps**: {console_url}?tab=apps
- **View workflow run history**: {console_url}?tab=runs
- **Browse datasets**: {console_url}?tab=datasets
- **View dashboards**: {console_url}?tab=dashboards

Use `nrev_open_console` to open any tab directly in the user's browser.

## ⛔ MANDATORY: Plan Before Execution

**You MUST show a plan and get user approval before calling ANY tool that costs credits.**
There are NO exceptions to this rule. Even single-operation requests require a plan.

### How It Works

**Step 1 — Silent balance check (user does NOT see this):**
Call `nrev_credit_balance`. Note the balance and topup_url. Do NOT show this as a step.

**Step 2 — Show the plan with cost estimate.**

For multi-step workflows (balance sufficient):

> Here's my plan:
>
> 1. Search for VP Sales at Series B SaaS companies — ~2 credits
> 2. Enrich top 20 results with email + phone — ~20 credits
> 3. Verify email deliverability — ~20 credits
>
> Estimated total: ~42 credits | Balance: 150 credits ✓
>
> Shall I proceed?

For multi-step workflows (balance insufficient):

> Here's my plan:
>
> 1. Search for VP Sales at Series B SaaS companies — ~2 credits
> 2. Enrich top 20 results with email + phone — ~20 credits
>
> Estimated total: ~22 credits
> ⚠ You have 5 credits — need ~22.
>
> Add credits: [topup_url from nrev_credit_balance]
> Or add your own API keys (free): `nrev-lite keys add apollo`

For single operations:

> This will use ~1 credit to enrich this person. Balance: 50 credits ✓ Proceed?

**Step 3 — WAIT. Do NOT proceed until the user explicitly confirms.**

### Plan Rules
- Every credit-costing tool call needs a plan. No exceptions.
- Single operations: one-line plan is fine (no need for bullet list)
- Multi-step workflows: 3-5 bullet points max, non-technical language
- Always include estimated credits per step AND total
- Always include current balance with ✓ (sufficient) or ⚠ (insufficient)
- If insufficient: ALWAYS include the topup_url — never just say "add credits"
- For batches >10 records: pilot 5 records first, show hit rate, then ask to continue
- BYOK operations are free — say "Free (using your own [provider] key)" instead of credit count
- If the user asks a follow-up question, answer it and ask again before proceeding

### Credit Costs Per Operation
- People search: ~2 credits per search
- Person enrichment: ~1 credit per person
- Company enrichment: ~1 credit per company
- Google search: ~1 credit per query
- Web scrape: ~1 credit per URL
- Email verification: ~1 credit per email
- Email finder: ~1 credit per lookup
- Company signals: ~1 credit per company
- AI research: ~1 credit per query
- BYOK calls: always free

### Tools That Do NOT Require a Plan (free)
nrev_health, nrev_credit_balance, nrev_estimate_cost, nrev_app_list,
nrev_app_catalog, nrev_app_connect, nrev_open_console, nrev_app_actions,
nrev_app_action_schema, nrev_app_execute (all app actions are free),
nrev_list_tables, nrev_list_datasets, nrev_query_dataset,
nrev_search_patterns, nrev_get_knowledge, nrev_new_workflow,
nrev_get_run_log, nrev_save_script, nrev_list_scripts, nrev_get_script

## Apps — Connected Tools (Free, No Credits)

Apps are external tools the user connects via OAuth (Gmail, Slack, HubSpot, etc.).
App actions are **free** — no credits charged.

**When the user mentions any app keyword:**
1. Check for system MCP tools first (e.g., `slack_send_message`) — use directly if available
2. If no system MCP tool, call `nrev_app_list()` to check connected apps
3. If not connected, call `nrev_app_catalog()` to show available apps, then `nrev_app_connect(app_id)` to set it up
4. Use the discovery flow: `nrev_app_actions` → `nrev_app_action_schema` → `nrev_app_execute`

**Never hardcode action names or parameters — always discover via `nrev_app_action_schema`.**

| User says... | app_id |
|---|---|
| email, Gmail, inbox, send email, draft | `gmail` |
| Slack, message, channel, DM | `slack` |
| Teams, Microsoft Teams | `microsoft_teams` |
| calendar, meeting, schedule, events | `google_calendar` |
| Calendly, booking link | `calendly` |
| Cal.com, scheduling | `cal_com` |
| spreadsheet, Google Sheet, add row | `google_sheets` |
| document, Google Doc, write doc | `google_docs` |
| Drive, upload file, Google Drive | `google_drive` |
| Airtable, base, airtable record | `airtable` |
| CRM, deal, contact record, pipeline, HubSpot | `hubspot` |
| Salesforce, opportunity, lead, SFDC | `salesforce` |
| Attio, CRM record | `attio` |
| cold email, outreach campaign, Instantly | `instantly` |
| task, ticket, issue, Linear | `linear` |
| Notion, notion page, notion doc | `notion` |
| ClickUp, clickup task | `clickup` |
| Asana, asana task | `asana` |
| Zoom, video call, webinar, recording | `zoom` |
| meeting notes, transcript, Fireflies | `fireflies` |
| product analytics, feature flags, PostHog | `posthog` |

## Data Providers vs Apps

| Need | Use |
|------|-----|
| Read/send user's emails | **Apps** (gmail) — free |
| Enrich contacts with phone/email | **Data Providers** (apollo) — credits |
| Push leads to user's CRM | **Apps** (hubspot) — free |
| Google search for company info | **Data Providers** (rapidapi) — credits |
| Scrape a website | **Data Providers** (parallel) — credits |
| Send cold email campaigns | **Apps** (instantly) — free |
| Check user's calendar | **Apps** (google_calendar) — free |
| AI research on a topic | **Data Providers** (perplexity) — credits |

## Troubleshooting

- `"Not authenticated"` → Run `nrev-lite auth login` in the terminal
- `"No active connection for 'X'"` → Connect the app: `nrev_app_connect(app_id="X")`
- `"Insufficient credits"` → Top up at {console_url}?tab=usage or add BYOK keys
- `"Cannot connect to server"` → Check if server is running, or run `nrev-lite status`
"""

    # ── Write CLAUDE.md ───────────────────────────────────────────────
    if scope_dir == Path.home() / ".claude":
        claude_md_path = scope_dir / "CLAUDE.md"
    else:
        claude_md_path = scope_dir.parent / "CLAUDE.md"

    scope_dir.mkdir(parents=True, exist_ok=True)

    if claude_md_path.exists():
        existing = claude_md_path.read_text()
        if "nrev-lite" in existing.lower() or "nrev_lite" in existing.lower():
            # Already has nrev-lite content — skip to avoid duplicates
            pass
        else:
            claude_md_path.write_text(existing.rstrip() + "\n\n" + claude_md_content)
    else:
        claude_md_path.write_text(claude_md_content)

    return True


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------


@click.command("init")
@click.option(
    "--project",
    is_flag=True,
    help="Register MCP server for this project only."
)
@click.option(
    "--skip-auth",
    is_flag=True,
    help="Skip authentication (if already logged in)."
)
@click.option(
    "--server-url",
    default=None,
    help="nrev-lite server URL (default: http://localhost:8000 or configured value)."
)
@click.option(
    "--force",
    is_flag=True,
    help="Re-register MCP server even if already registered."
)
@click.option(
    "--vscode",
    is_flag=True,
    help="Also register MCP server in VSCode settings.",
)
def init(project: bool, skip_auth: bool, server_url: str | None, force: bool, vscode: bool) -> None:
    """Set up nrev-lite for Claude Code in one command.

    \b
    This command:
      1. Authenticates you via Google (opens browser)
      2. Registers the nrev-lite MCP server with Claude Code
      3. Installs Claude Code knowledge (so Claude knows when to use nrev-lite)
      4. Verifies the connection works

    \b
    After running this, every new Claude Code session will have access to
    all nrev-lite tools AND Claude will know when to use them.

    \b
    Examples:
        nrev-lite init                    # Full setup (global)
        nrev-lite init --project          # Project-level only
        nrev-lite init --skip-auth        # Already logged in, just register MCP
        nrev-lite init --force            # Re-register even if already set up
        nrev-lite init --vscode           # Also register in VSCode
        nrev-lite init --server-url https://api.nrev.dev
    """
    click.echo()
    click.secho("  nrev-lite — Agent-Native GTM Platform", fg="cyan", bold=True)
    click.secho("  ─────────────────────────────────", fg="cyan")
    click.echo()

    # ── Pre-check: Claude Code CLI must be available ──────────────────
    if not shutil.which("claude"):
        print_error(
            "Claude Code CLI not found on PATH.\n"
            "  Install it from: https://claude.ai/download\n"
            "  Then run `nrev-lite init` again."
        )
        sys.exit(1)

    # ── Step 0: Configure server URL if provided ──────────────────────
    if server_url:
        from nrev_lite.utils.config import set_config
        set_config("server.url", server_url.rstrip("/"))
        click.echo(f"  Server URL set to: {server_url}")
        click.echo()

    # ── Step 1: Authentication ────────────────────────────────────────
    click.secho("  Step 1/4 — Authentication", bold=True)

    if skip_auth and is_authenticated():
        creds = load_credentials()
        email = (creds or {}).get("user_info", {}).get("email", "unknown")
        click.echo(f"  Already logged in as {email}")
    elif is_authenticated():
        creds = load_credentials()
        email = (creds or {}).get("user_info", {}).get("email", "unknown")
        click.echo(f"  Already logged in as {email}")

        if not click.confirm("  Use existing session?", default=True):
            click.echo("  Opening browser for authentication...")
            from nrev_lite.cli.auth import _browser_oauth_flow
            _browser_oauth_flow(get_api_base_url())
    else:
        click.echo("  Opening browser for Google authentication...")
        click.echo()
        from nrev_lite.cli.auth import _browser_oauth_flow
        _browser_oauth_flow(get_api_base_url())

    # Verify auth succeeded
    if not is_authenticated():
        print_error("Authentication failed. Run `nrev-lite auth login` manually.")
        sys.exit(1)

    creds = load_credentials()
    email = (creds or {}).get("user_info", {}).get("email", "unknown")
    tenant = (creds or {}).get("user_info", {}).get("tenant", "unknown")
    print_success(f"Authenticated as {email} (tenant: {tenant})")
    click.echo()

    # ── Step 2: Register MCP server via `claude mcp add` ─────────────
    scope = "local" if project else "user"
    scope_label = "this project" if project else "all Claude Code sessions"

    click.secho("  Step 2/4 — Register MCP Server", bold=True)
    click.echo(f"  Scope: {scope_label}")

    if _is_already_registered() and not force:
        print_success("nrev-lite MCP server already registered")
    else:
        if force and _is_already_registered():
            click.echo("  Re-registering (--force)...")
            _unregister_mcp_server(scope)

        if _register_mcp_server(scope):
            print_success("MCP server registered via `claude mcp add`")
        else:
            sys.exit(1)

    # VSCode registration — when --vscode flag is passed or VSCode terminal detected
    use_vscode = vscode or _is_vscode_terminal()
    if use_vscode:
        vscode_settings = _get_vscode_settings_path()
        click.echo()
        click.echo("  VSCode detected — registering MCP server in VSCode settings")

        # Register in VSCode global settings.json
        if _register_mcp_vscode(vscode_settings):
            print_success(f"MCP server registered in VSCode ({vscode_settings})")
        else:
            print_warning("Failed to register MCP server in VSCode settings.")

        # Also register project-level .mcp.json for VSCode project discovery
        if _register_mcp_project():
            project_path = Path.cwd() / ".mcp.json"
            print_success(f"MCP server registered in project ({project_path})")
        else:
            print_warning("Failed to register project-level .mcp.json.")
    elif not vscode:
        click.echo()
        click.echo("  Tip: Using VSCode? Run 'nrev-lite init --vscode' to register there too.")

    click.echo()

    # ── Step 3: Install Claude Code knowledge ─────────────────────────
    click.secho("  Step 3/4 — Install Claude Code Knowledge", bold=True)
    click.echo("  Teaching Claude when and how to use nrev-lite...")

    base_url = get_api_base_url()
    scope_dir = (Path.cwd() / ".claude") if project else (Path.home() / ".claude")

    if _install_claude_integration(scope_dir, base_url, tenant):
        print_success("CLAUDE.md installed — Claude now knows when to use nrev-lite")
    else:
        print_warning("Could not install CLAUDE.md. Run `nrev-lite setup-claude` manually.")

    click.echo()

    # ── Step 4: Verify connection ─────────────────────────────────────
    click.secho("  Step 4/4 — Verify Connection", bold=True)

    if _verify_server_reachable():
        print_success("Server is reachable")
    else:
        print_warning(
            f"Server at {base_url} is not reachable right now.\n"
            "  That's OK — the MCP server will connect when the API is running."
        )

    # ── Done ──────────────────────────────────────────────────────────
    console_url = f"{base_url}/console/{tenant}"
    click.echo()
    click.secho("  ─────────────────────────────────", fg="green")
    click.secho("  Setup complete!", fg="green", bold=True)
    click.echo()
    click.echo("  What happens now:")
    click.echo("  • Open a new Claude Code session (or restart the current one)")
    click.echo("  • Claude will automatically have access to all nrev-lite tools")
    click.echo("  • Claude knows when to use them — just describe what you need")
    click.echo()
    click.echo("  Try asking:")
    click.echo('    "Find 50 VP Sales at Series B SaaS companies"')
    click.echo('    "What apps can I connect?"')
    click.echo('    "Show me my credit balance"')
    click.echo()
    click.echo(f"  Your dashboard: {console_url}")
    click.echo()
