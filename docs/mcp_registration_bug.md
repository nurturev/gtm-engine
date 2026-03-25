# Bug: `nrev-lite init` registered MCP server in the wrong file

## Status
**Fixed** in `fix/mcp-init-registration` branch.

## Summary

`nrev-lite init` was writing the MCP server config to `~/.claude/settings.json` under `mcpServers`. Claude Code does **not** read MCP server definitions from that file — it only reads permissions and hooks from it. MCP servers must be registered via `claude mcp add`, which writes to `~/.claude.json`.

Result: init completed successfully, reported "MCP server registered", but the tools never appeared in Claude Code.

## Root Cause

Claude Code has two config files with similar names:

| File | What Claude Code reads from it | Managed by |
|------|-------------------------------|------------|
| `~/.claude/settings.json` | Permissions (`allow`/`deny`), hooks, status line, plugins | User-editable JSON |
| `~/.claude.json` | MCP servers, project-scoped settings, tool approvals | `claude mcp add/remove` CLI |

`nrev-lite init` wrote to the first file. It needed to use `claude mcp add` which writes to the second.

## Who was affected

Every new user running `nrev-lite init`. The MCP server silently failed to register. The init command reported success, user restarted Claude Code, and no nrev-lite tools appeared.

## Additional issue: missing `mcp` SDK dependency

`pyproject.toml` was missing the `mcp` Python package as a dependency. Without it, the MCP server fell back to a raw JSON-RPC implementation which Claude Code could not communicate with properly. Fixed by adding `mcp>=1.0.0` to dependencies.

## What was fixed

1. **`src/nrev_lite/cli/init.py`**: Replaced direct JSON file writing with `subprocess.run(["claude", "mcp", "add", ...])`. This uses Claude Code's own CLI to register the server in the correct location.

2. **`pyproject.toml`**: Added `mcp>=1.0.0` to dependencies so the MCP server uses the official SDK instead of the raw JSON-RPC fallback.

3. Added pre-check that `claude` CLI is on PATH before attempting registration.

4. Added `--force` flag to re-register if needed.

## For existing users who hit this bug

```bash
# Remove the stale entry from settings.json (optional cleanup)
# Then register correctly:
claude mcp add -s user nrev-lite -- nrev-lite mcp serve

# Verify
claude mcp list
# Should show: nrev-lite: ... - Connected

# Restart Claude Code session
```
