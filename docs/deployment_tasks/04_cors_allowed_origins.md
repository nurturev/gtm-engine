# Task 04: Add CORS Allowed Origins Configuration

**Status:** Not Started
**Priority:** P0 — Production CORS is currently broken (blocks all origins)
**Deployment Doc Reference:** Section 10

---

## Goal

Add a `CORS_ALLOWED_ORIGINS` environment variable so production CORS can be configured per environment, instead of the current behavior of blocking all origins in production.

---

## What to Change

### 1. Add config setting

In `server/core/config.py`, add:
```python
CORS_ALLOWED_ORIGINS: str = ""  # Comma-separated list of allowed origins
```

### 2. Update CORS middleware in `server/app.py`

Replace the current CORS setup with:
```python
if settings.ENVIRONMENT == "development":
    origins = ["*"]
else:
    origins = [o.strip() for o in settings.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## Files to Modify

| File | Change |
|------|--------|
| `server/core/config.py` | Add `CORS_ALLOWED_ORIGINS` setting |
| `server/app.py` | Update CORS middleware to use the new setting |

---

## Environment Variable

Follows org Helm pattern: injected as plain `value:` in `values-{env}.yaml` (not a secret).

```bash
# Staging (matches org DNS: {service}.public.{env}.nurturev.com)
CORS_ALLOWED_ORIGINS=https://nrev-lite-api.public.staging.nurturev.com

# Prod
CORS_ALLOWED_ORIGINS=https://nrev-lite-api.public.prod.nurturev.com

# Multiple origins (if needed later)
CORS_ALLOWED_ORIGINS=https://nrev-lite-api.public.prod.nurturev.com,https://console.nrev.ai
```

---

## Acceptance Criteria

- [ ] `CORS_ALLOWED_ORIGINS` env var is read from config
- [ ] Development mode (`ENVIRONMENT=development`) allows all origins (unchanged behavior)
- [ ] Production mode uses only the specified origins
- [ ] Console dashboard (served from same domain) still works in production
- [ ] Cross-origin requests from unlisted origins are blocked in production

---

## Testing

```bash
# Development: should allow any origin
curl -H "Origin: http://random-site.com" -I http://localhost:8000/health
# Should include Access-Control-Allow-Origin header

# Production: should only allow configured origin
ENVIRONMENT=production CORS_ALLOWED_ORIGINS=https://allowed.com \
  uvicorn server.app:app --port 8001 &
curl -H "Origin: https://allowed.com" -I http://localhost:8001/health
# Should include Access-Control-Allow-Origin: https://allowed.com
curl -H "Origin: https://blocked.com" -I http://localhost:8001/health
# Should NOT include Access-Control-Allow-Origin header
```
