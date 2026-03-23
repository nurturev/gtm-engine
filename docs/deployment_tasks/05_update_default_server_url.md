# Task 05: Update Default CLI Server URL

**Status:** Not Started
**Priority:** P0 — Without this, every CLI user must manually configure the server URL
**Deployment Doc Reference:** Section 10

---

## Goal

Change the default API base URL in the CLI from `http://localhost:8000` to the production server URL so that users don't need to manually configure it after `pip install nrev-lite`.

---

## What to Change

In `src/nrev_lite/utils/config.py`, line 16:

```python
# Change from:
DEFAULT_API_BASE_URL = "http://localhost:8000"

# Change to:
DEFAULT_API_BASE_URL = "https://nrev-lite-api.public.prod.nurturev.com"
```

---

## Files to Modify

| File | Change |
|------|--------|
| `src/nrev_lite/utils/config.py:16` | Update `DEFAULT_API_BASE_URL` constant |

---

## Important Notes

- Users can still override via `nrev-lite config set server.url <url>` or `NREV_LITE_SERVER_URL` env var
- Local development continues to work by setting: `nrev-lite config set server.url http://localhost:8000`
- The `get_api_base_url()` function checks user config first, falling back to this default — so existing dev setups with config files are unaffected
- **Do NOT change this until the production server is deployed and accessible** — otherwise the CLI will fail for everyone

---

## Acceptance Criteria

- [ ] `DEFAULT_API_BASE_URL` points to production domain
- [ ] `nrev-lite auth login` works without manual server URL config (after prod deploy)
- [ ] `nrev-lite config set server.url http://localhost:8000` still overrides for local dev
- [ ] `NREV_LITE_SERVER_URL` env var still overrides for CI/testing

---

## Execution Timing

This change should be the **last code change** before the PyPI publish, done only after Task 10 (first deploy) confirms the production server is healthy.
