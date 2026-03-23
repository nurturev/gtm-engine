# Task 01: Move Auth State to Redis

**Status:** Not Started
**Priority:** P0 — Blocking for production (multi-replica safety)
**Deployment Doc Reference:** Sections 7, 8

---

## Goal

Replace two in-memory Python dicts in `server/auth/router.py` with Redis-backed storage so that auth state survives pod restarts and works across multiple replicas.

---

## What to Change

### 1. Move `_pending_auth` to Redis

**Current:** In-memory dict storing OAuth state during the Google auth flow.

**Target:**
- Key pattern: `auth:pending:{state}` (state = OAuth state parameter)
- Value: JSON string containing `{cli_redirect, code_verifier, console_login}`
- TTL: 600 seconds (10 minutes)
- On `POST /api/v1/auth/google`: SET to Redis with TTL
- On `GET /api/v1/auth/callback`: GET + DELETE from Redis

### 2. Move `_device_codes` to Redis

**Current:** In-memory dict storing device code auth state for headless CLI flow.

**Target:**
- Key pattern: `auth:device:{device_code}`
- Value: JSON string containing `{user_code, expires_at, user_id, tenant_id, completed}`
- TTL: 900 seconds (15 minutes)
- On `POST /api/v1/auth/device/code`: SET to Redis with TTL
- On device verification: GET, update `completed=True`, SET back
- On `POST /api/v1/auth/device/token` (poll): GET from Redis, return 428 if not completed

---

## Files to Modify

| File | Change |
|------|--------|
| `server/auth/router.py` | Replace `_pending_auth` dict and `_device_codes` dict with Redis GET/SET/DELETE calls |

---

## Implementation Notes

- The Redis connection is already established at app startup in `server/app.py` and available as `app.state.redis`
- Use `json.dumps()` / `json.loads()` for serialization (values are simple dicts)
- Existing Redis usage in `server/execution/cache.py` can serve as a reference pattern
- The Redis instance is passed through FastAPI's `request.app.state.redis` — check how existing code accesses it and follow the same pattern

### Org Pattern Reference (Workflow Studio)

nrev-lite's Redis usage differs from Workflow Studio's pattern (SingletonBorg, separate `REDIS_HOST`/`REDIS_PORT`, centralized key prefixes in `constants/redis_constants.py`). For V1, keep nrev-lite's existing `redis.asyncio` approach. V2 will align with org patterns.

Reference: `workflow_studio/infrastructure/async_redis_client.py`, `workflow_studio/constants/redis_constants.py`

### ElastiCache Connectivity

nrev-lite reuses existing org ElastiCache clusters (not new instances):
- Staging: `staging-cache-sooatg.serverless.aps1.cache.amazonaws.com:6379` (TLS)
- Prod: `prod-cache-msnit6.serverless.use1.cache.amazonaws.com:6379` (TLS)

Ensure the `REDIS_URL` env var uses `rediss://` (double-s) for TLS connections.

---

## Acceptance Criteria

- [ ] `_pending_auth` dict removed from `server/auth/router.py`
- [ ] `_device_codes` dict removed from `server/auth/router.py`
- [ ] OAuth login flow works end-to-end with Redis (test: `nrev-lite auth login`)
- [ ] Device code flow works end-to-end with Redis (test: headless auth)
- [ ] State expires after TTL (no stale entries accumulate)
- [ ] Server restart mid-auth-flow does not lose pending state

---

## Testing

```bash
# 1. Start server with Redis
docker-compose up -d redis
uvicorn server.app:app --reload

# 2. Test OAuth flow
nrev-lite auth login
# Should complete successfully

# 3. Test state persistence: start auth, restart server, complete auth
nrev-lite auth login &  # Opens browser
# Restart server while browser is on Google consent screen
# Complete Google auth — callback should still work

# 4. Test TTL: start auth, wait >10 minutes, try callback
# Should return error (state expired)
```
