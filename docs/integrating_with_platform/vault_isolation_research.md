# Vault Module Isolation Research: BYOK Keys and Consultant Agent

## Status: Decision Made — BYOK Blocked for Consultant Agent

---

## 1. Decision

**BYOK keys are CLI-only.** The consultant agent will not use BYOK keys, even for tenants who have added them via CLI. The consultant agent must either use platform API keys or skip providers that lack platform keys.

### Rationale

- BYOK is a CLI-specific feature. The main app platform has its own key management and automation suite that doesn't integrate with gtm-engine's vault.
- Allowing BYOK in the consultant agent creates a dependency on a CLI feature from the platform side — unclear ownership and support boundaries.
- When BYOK is eventually rebuilt inside the application layer, having consultant agent users accustomed to gtm-engine BYOK creates a migration headache.
- Credit accounting becomes inconsistent: same operation costs credits from the platform but is free if the user happened to set up a BYOK key via CLI.

---

## 2. Current Vault Architecture

### Module Structure

```
server/vault/
  models.py    → TenantKey ORM model (tenant_id + provider + encrypted_key)
  service.py   → encrypt_key(), decrypt_key(), key_hint()
  schemas.py   → Pydantic request/response schemas
  router.py    → CRUD endpoints at /api/v1/keys
```

### How BYOK Keys Are Stored

Keys are stored in the `tenant_keys` table:

| Column | Type | Purpose |
|--------|------|---------|
| `id` | INTEGER | PK |
| `tenant_id` | TEXT | Tenant isolation (RLS-protected) |
| `provider` | TEXT | Provider name (apollo, rapidapi_google, etc.) |
| `encrypted_key` | BYTEA | Encrypted API key blob |
| `key_hint` | TEXT | Last 4 chars for identification |
| `status` | TEXT | "active" or inactive |
| `created_at` | TIMESTAMPTZ | Creation time |

Unique constraint: `(tenant_id, provider)` — one key per provider per tenant.

### Encryption Strategy

- **Development**: Fernet symmetric encryption. Key derived via PBKDF2(JWT_SECRET_KEY, tenant_id, 100k iterations). Per-tenant derivation means compromising one tenant's keys doesn't expose another's.
- **Production**: AWS KMS envelope encryption with `tenant_id` in the encryption context. Decryption requires both KMS access AND the correct tenant_id.

### Where BYOK Keys Are Consumed

Exactly two places in the codebase import from the vault:

1. **Vault router** (`server/vault/router.py`) — CRUD for key management. CLI and console only.
2. **Execution service** (`server/execution/service.py:246-281`) — `resolve_api_key()` function. This is the critical integration point.

### The Key Resolution Function

```python
# server/execution/service.py:246
async def resolve_api_key(
    db: AsyncSession,
    tenant_id: str,
    provider_name: str,
) -> tuple[str, bool]:
    """Returns (api_key, is_byok). Checks BYOK first, then platform keys."""

    # 1. Check BYOK first
    result = await db.execute(
        select(TenantKey).where(
            TenantKey.tenant_id == tenant_id,
            TenantKey.provider == provider_name,
            TenantKey.status == "active",
        )
    )
    byok = result.scalar_one_or_none()
    if byok is not None:
        api_key = decrypt_key(byok.encrypted_key, tenant_id)
        return api_key, True  # is_byok = True → no credits charged

    # 2. Fall back to platform key
    platform_key = _PLATFORM_KEYS.get(provider_name)
    if platform_key:
        return platform_key, False  # is_byok = False → credits charged

    raise ProviderError(...)
```

**This function is currently channel-agnostic.** It takes `tenant_id` and `provider_name` — it has no knowledge of whether the call originated from CLI or consultant agent. This is why BYOK would flow through to the consultant agent without changes, and why changes are needed to block it.

### How `resolve_api_key` Gets Called

```
POST /api/v1/execute
  → execute_operation() [router.py]
    → execute_single() [service.py:284]
      → resolve_api_key(db, tenant_id, provider_name) [service.py:246]
```

The `tenant_id` comes from the JWT via `get_current_tenant()`. The `channel` claim (planned in data_module_extension_plan.md §4.3) will be in the JWT but is **not currently passed** to `resolve_api_key()` or `execute_single()`.

---

## 3. BYOK Awareness Across Skills and MCP Tools

BYOK awareness is not limited to the server-side execution pipeline. It surfaces in three layers: the server, the MCP tools (which the LLM calls), and the skills (which shape the LLM's reasoning and user-facing messaging). All three layers must be consistent with the decision to block BYOK for the consultant agent.

### 3.1 Skills with BYOK Awareness

**`gtm-builder/SKILL.md`** (lines 73-84) — **Direct BYOK references in user-facing messaging**

This is the primary workflow skill. It actively guides users toward BYOK in two places:

1. When credits are insufficient (line 74):
   ```
   → Or add your own API keys (free): `nrev-lite keys add <provider>`
   ```
2. When balance is 0 with no BYOK keys (lines 77-84):
   ```
   You don't have any credits or API keys set up yet.
   → Or bring your own API key (always free): `nrev-lite keys add apollo`
   Once you have credits or keys, I'll run this workflow for you.
   ```

This skill shapes the LLM's behavior when it encounters low/zero credit situations. The LLM will suggest BYOK as an alternative to buying credits.

**`scraping-tools/SKILL.md`** (line 92) — **Minor, documentation-only reference**
- `Stored as PARALLEL_KEY in .env or vault.` — informational note about key storage location. No behavioral impact.

**All other skills** (`provider-selection`, `waterfall-enrichment`, `list-building`, `apollo-enrichment`, `rocketreach-enrichment`, `google-search`, `gtm-consultant/*`, `composio-connections`, `humanizer`, `parallel-research`, `instantly-campaigns`, `tool-skills/*`) have **zero BYOK awareness**. They reference provider APIs, auth headers, and quirks, but never distinguish between BYOK and platform keys.

### 3.2 MCP Tools with BYOK Awareness

**`nrev_estimate_cost`** (`src/nrev_lite/mcp/server.py:496-1518`) — **Critical: actively checks BYOK and changes cost output**

This tool is called before every workflow execution (per the mandatory plan approval flow). It:
1. Calls `GET /keys` to check if the tenant has a BYOK key for the operation's provider
2. If BYOK exists: returns `estimated_credits: 0`, note: `"Free — using your own {provider} API key"`
3. If no BYOK: returns actual credit cost and dollar estimate

The LLM uses this output to build the cost plan shown to the user. If BYOK is present, the plan says "0 credits (free)".

**`nrev_provider_status`** (`src/nrev_lite/mcp/server.py:539-1297`) — **Reports BYOK vs platform per provider**

Returns per-provider status including:
- `key_source: "byok"` or `"platform"` for each provider
- `byok_keys` count (total number of BYOK keys the tenant has)

The LLM uses this to understand which providers are available and how they're configured.

**`nrev_credit_balance`** (`src/nrev_lite/mcp/server.py:527-530`) — **Informational BYOK mention**

Tool description states: `"BYOK (bring-your-own-key) calls are free; platform key calls cost credits."` The response also includes a `_tip` field (line 1260-1262): `"Or add your own API keys (free): nrev-lite keys add <provider>"`. This tip is shown when credits are low.

### 3.3 CLI with BYOK Awareness (Not Relevant to Consultant)

For completeness: `cli/status.py` shows BYOK badges, `cli/enrich.py` mentions "BYOK = free" in cost estimates, `cli/keys.py` is the key management CLI. These are CLI-only and unaffected.

---

## 4. Implementation Plan

### Prerequisite: `channel` Claim in JWT

All changes below are **blocked by** the `channel` claim implementation (data_module_extension_plan.md §4.3). The `channel` must be:
1. Set during token exchange (`"cli"` or `"consultant"`)
2. Included in the gtm-engine JWT claims
3. Extracted by middleware into `request.state.channel`

Without this, there is no way to distinguish CLI calls from consultant calls at the execution or MCP tool layer.

### 4.1 Server Layer Changes

#### S1: Pass `channel` through the execution call chain

The `channel` claim will already be in the JWT and extracted into `request.state.channel` by middleware. It needs to flow through:

```
execute_operation() [router.py]
  → reads request.state.channel
  → passes channel to execute_single()
    → passes channel to resolve_api_key()
```

**Files touched:**

| File | Change |
|------|--------|
| `server/execution/service.py` | Add `channel: str \| None = None` param to `resolve_api_key()` and `execute_single()` |
| `server/execution/router.py` | Extract `request.state.channel`, pass to `execute_single()` |

#### S2: Skip BYOK lookup when channel is "consultant"

```python
# server/execution/service.py — resolve_api_key()

async def resolve_api_key(
    db: AsyncSession,
    tenant_id: str,
    provider_name: str,
    channel: str | None = None,
) -> tuple[str, bool]:
    # Skip BYOK for consultant agent — CLI-only feature
    if channel != "consultant":
        result = await db.execute(
            select(TenantKey).where(
                TenantKey.tenant_id == tenant_id,
                TenantKey.provider == provider_name,
                TenantKey.status == "active",
            )
        )
        byok = result.scalar_one_or_none()
        if byok is not None:
            api_key = decrypt_key(byok.encrypted_key, tenant_id)
            return api_key, True

    # Platform key fallback (used by all channels)
    platform_key = _PLATFORM_KEYS.get(provider_name)
    if platform_key:
        return platform_key, False

    raise ProviderError(...)
```

#### S3 (Optional, defense-in-depth): Block vault CRUD endpoints for consultant channel

The vault router (`/api/v1/keys`) handles key add/list/delete. The consultant agent orchestrator should never call these (it's not in its API whitelist), but as defense-in-depth:

```python
# server/vault/router.py — add to each endpoint

if getattr(request.state, "channel", None) == "consultant":
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="BYOK key management is available via CLI only",
    )
```

**This is optional for V1** — the orchestrator whitelist is sufficient. But if you want server-enforced gating, this is where it goes.

### 4.2 MCP Tools / API Layer Changes

This is the critical layer. If only the server blocks BYOK but the MCP tools still report BYOK as active, the consultant agent's LLM will show incorrect cost estimates and misleading provider status to users.

#### M1 (Critical): `GET /keys` returns empty list for consultant channel

**The problem:** `nrev_estimate_cost` calls `GET /keys`, checks if the tenant has a BYOK key for the provider, and returns `estimated_credits: 0` with the note `"Free — using your own {provider} API key"`. Without this fix, the consultant agent would show the user "this is free" in the plan, but when it actually executes, the server skips BYOK and charges credits via the platform key. **The estimate and the actual cost disagree.**

**The fix:** Make `GET /api/v1/keys` channel-aware. When `channel=consultant`, return an empty key list. The MCP tool's existing BYOK check logic finds no keys and returns the correct platform-key cost estimate.

```python
# server/vault/router.py — list_keys()

@router.get("/keys", response_model=KeyListResponse)
async def list_keys(
    request: Request,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
) -> KeyListResponse:
    channel = getattr(request.state, "channel", None)
    if channel == "consultant":
        return KeyListResponse(keys=[])
    # ... existing BYOK key listing logic
```

#### M2 (Automatic): `nrev_provider_status` cascades from M1

The `nrev_provider_status` handler calls `GET /keys` to determine BYOK presence. If `GET /keys` returns an empty list for consultant channel (per M1), then `nrev_provider_status` will naturally report all providers as `key_source: "platform"` and `byok_keys: 0`. **No additional change needed.**

#### M3 (Low priority): `nrev_credit_balance` tip suppression

The `_tip` field says: `"Or add your own API keys (free): nrev-lite keys add <provider>"`. This is CLI-specific advice.

Since the MCP server is a CLI-side component and the consultant agent will call REST endpoints directly, this tip filtering would happen at the orchestrator level. **Low priority for V1.**

### 4.3 Skills Layer Changes

The consultant agent will have its own orchestrator and prompting — CLI skills are not directly loaded into the consultant. No changes needed to existing CLI skills (`gtm-builder/SKILL.md`, `scraping-tools/SKILL.md`).

**Design requirement for consultant agent prompting:** When credits are insufficient, the consultant should only show the topup URL or direct users to the platform's credit purchase flow. No mention of BYOK keys, no `nrev-lite keys add` suggestions.

### 4.4 Change Summary

#### Server Layer

| # | Change | File(s) | Size | Blocked By |
|---|--------|---------|------|-----------|
| S1 | Add `channel` param to `resolve_api_key()` and `execute_single()` | `server/execution/service.py` | ~8 lines | `channel` claim in JWT |
| S2 | Skip BYOK lookup when `channel == "consultant"` | `server/execution/service.py` | ~2 lines (conditional wrap) | S1 |
| S3 | Pass `channel` from router to service | `server/execution/router.py` | ~4 lines | `channel` claim in JWT |
| S4 | (Optional) Block vault CRUD for consultant | `server/vault/router.py` | ~10 lines | `channel` claim in JWT |

#### MCP Tools / API Layer

| # | Change | File(s) | Size | Priority | Blocked By |
|---|--------|---------|------|----------|-----------|
| M1 | `GET /keys` returns empty list for consultant channel | `server/vault/router.py` | ~5 lines | **Critical** — without this, cost estimates are wrong | `channel` claim in JWT |
| M2 | `nrev_provider_status` reports all providers as "platform" for consultant | — (cascades from M1) | 0 lines | Automatic | M1 |
| M3 | `nrev_credit_balance` tip omits BYOK suggestion for consultant | Orchestrator-level filtering | ~5 lines | Low | Orchestrator design |

#### Skills Layer

| # | Change | File(s) | Size | Priority |
|---|--------|---------|------|----------|
| K1 | Consultant agent prompting must not include BYOK suggestions | Consultant agent's own prompt files | Design decision | **Required** |
| K2 | No change to CLI skills | — | 0 | N/A |

**Total server changes: ~15-25 lines.** Total MCP/API changes: ~5 lines. Total skill changes: design decision for consultant prompting (no CLI code changes needed).

---

## 5. Edge Cases and Considerations

### Edge Case: Tenant Has BYOK Key But No Platform Key

If a tenant added a BYOK key for a provider (e.g., PredictLeads) but there is no platform key for that provider, `resolve_api_key()` skips BYOK, finds no platform key, and raises `ProviderError`. The consultant agent gets a "No API key found" error.

**Mitigation**: The error message should be channel-aware — instead of suggesting `nrev-lite keys add {provider}`, it should say "This provider is not available via the consultant agent." Alternatively: ensure all providers used by the consultant agent have platform keys configured.

### Edge Case: Credit Charging Difference

Same tenant, same enrichment call:
- Via CLI with BYOK: free (0 credits)
- Via consultant agent: costs 1 credit (platform key)

This is intentional. The credit dashboard should show channel-level breakdowns (already planned in data_module_extension_plan.md §8) so users understand why the same operation has different costs.

### Edge Case: Batch Operations

`execute_batch()` in the router calls `execute_single()` per item. If `channel` is threaded through `execute_single()`, batch operations automatically respect the BYOK block. No separate handling needed.

### Edge Case: Cost Estimate → Execution Mismatch

This is the most important edge case and the reason M1 is marked critical.

Without M1, the following sequence occurs:
1. Consultant agent calls `nrev_estimate_cost("enrich_person", 10)`
2. MCP tool calls `GET /keys`, finds tenant's Apollo BYOK key
3. Returns `estimated_credits: 0`, note: `"Free — using your own apollo API key"`
4. Consultant shows user: "This will cost 0 credits (free)"
5. User approves
6. Consultant calls `POST /api/v1/execute` — server skips BYOK (channel=consultant), uses platform key
7. Server charges 10 credits
8. User sees unexpected credit deduction

With M1, step 2 returns an empty key list → step 3 returns `estimated_credits: 10` → the plan is accurate.

### Consideration: Key Management Endpoints Remain CLI-Only Regardless

The vault router (`/api/v1/keys` CRUD) is a CLI/console feature. The consultant agent orchestrator does not need to manage BYOK keys — that's done by the user via CLI. Key management stays CLI-only. The change is that the execution pipeline **does not use** those keys when called by the consultant agent.

### Consideration: No Impact on Vault Module Internals

The vault module itself (`service.py`, `models.py`, `schemas.py`) requires **zero changes**. The gating happens at the call site (`resolve_api_key` in execution service) and at the API response layer (`GET /keys`), not inside the vault. The vault remains a clean, channel-agnostic encryption/decryption library.

---

## 6. Relationship to Other Migration Docs

| Doc | Relationship to Vault |
|-----|----------------------|
| `auth_migration_research.md` | Vault router depends on `get_current_tenant()` — when this migrates to JWT-only, vault router gets the same mechanical update as every other router. No vault-specific concern. |
| `enrichment_integration_research.md` | §4.4 currently states BYOK is shared per-tenant across CLI and consultant. **Needs revision** — §4.4 should be updated to reflect that BYOK is CLI-only and the consultant agent always uses platform keys. |
| `data_module_extension_plan.md` | §4.3 defines the `channel` claim — this is the prerequisite for the BYOK block. §5.2 mentions feature gating by channel — vault CRUD blocking follows the same pattern. |
| `composio_isolation_research.md` | Composio is already blocked from consultant agent by orchestrator whitelist. Vault follows the same gating pattern. |

---

## 7. Future: BYOK for Non-Platform Providers

Eventually, the platform may allow BYOK keys for providers that do not have a native integration in the main app. For example, if a tenant uses a niche data provider that the platform does not offer as a built-in integration, they could bring their own API key for that provider and use it through the consultant agent.

This would require:
- A BYOK key management UI inside the main platform (separate from the CLI vault)
- A provider allowlist: only providers without a platform integration can accept BYOK keys
- Integration with the platform's own key storage and encryption infrastructure (not gtm-engine's vault)

**This is not part of the immediate plan.** The details will be defined when the need arises. For now, all consultant agent calls use platform keys exclusively.