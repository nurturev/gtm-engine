# Task 02: Rate Limit Auth Endpoints

**Status:** Not Started
**Priority:** P1 — Security hardening
**Depends On:** Task 01 (Redis must be available for auth)
**Deployment Doc Reference:** Section 8

---

## Goal

Add rate limiting on the device code polling endpoint to prevent brute-force attacks on device codes.

---

## What to Change

### Rate limit `POST /api/v1/auth/device/token`

**Why:** The device code flow uses a short user_code that could be brute-forced. Without rate limiting, an attacker could rapidly poll different codes.

**Target:**
- Reuse existing rate limiter from `server/execution/rate_limiter.py`
- Key pattern: `ratelimit:auth:device:{client_ip}`
- Limit: 10 requests per minute
- On limit exceeded: return 429 Too Many Requests with `Retry-After` header

---

## Files to Modify

| File | Change |
|------|--------|
| `server/auth/router.py` | Add rate limiting check before device token polling logic |

---

## Implementation Notes

- `server/execution/rate_limiter.py` already implements a Redis-based token bucket algorithm via Lua script
- May need to extract the rate limiter into a shared location (e.g., `server/core/rate_limiter.py`) or import it directly
- Get client IP from `request.client.host` — be aware of proxy headers (`X-Forwarded-For`) when behind nginx ingress
- Consider also rate limiting `POST /api/v1/auth/google` (OAuth initiation) at 20 req/min per IP

---

## Acceptance Criteria

- [ ] Device token endpoint returns 429 after 10 rapid requests from same IP
- [ ] Rate limit resets after 1 minute
- [ ] Normal polling cadence (every 5 seconds) is not affected
- [ ] Rate limiter uses Redis (not in-memory)

---

## Testing

```bash
# Rapid-fire 15 requests — last 5 should get 429
for i in $(seq 1 15); do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST \
    http://localhost:8000/api/v1/auth/device/token \
    -H "Content-Type: application/json" \
    -d '{"device_code": "fake-code"}'
done
# Expected: first 10 return 404 (invalid code), last 5 return 429
```
