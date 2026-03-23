# Task 06: Update Dockerfile for Production

**Status:** Not Started
**Priority:** P0 — Required before building the production image
**Deployment Doc Reference:** Section 4

---

## Goal

Update `Dockerfile.server` with production optimizations: multiple workers and migrations bundled in the image.

### Org Pattern Reference (Workflow Studio)

Workflow Studio uses a multi-stage Dockerfile with gunicorn + uvicorn workers, hash-verified deps (`pip-tools`, `--require-hashes`), port 8080, and worker recycling (`--max-requests 1000 --max-requests-jitter 100`). See: `workflow_studio/Dockerfile_server`.

**For V1, keep nrev-lite's single-stage build with direct uvicorn on port 8000.** Multi-stage + gunicorn is a V2 item. The Helm chart maps port 80→8000, so external behavior is identical regardless of container port.

---

## What to Change

Update `Dockerfile.server` to:

```dockerfile
FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

COPY server/ server/
COPY migrations/ migrations/

EXPOSE 8000

# Production: multiple workers, no reload
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
```

---

## Changes from Current

| Change | Why |
|--------|-----|
| Add `COPY migrations/ migrations/` | Migrations available inside container for ad-hoc apply |
| Add `--workers 2` | Match 500m CPU limit (~1 worker per 250m CPU). Single worker can't saturate the pod. |

---

## Files to Modify

| File | Change |
|------|--------|
| `Dockerfile.server` | Add migrations COPY and --workers 2 |

---

## Acceptance Criteria

- [ ] `docker build -f Dockerfile.server .` succeeds
- [ ] Container starts with 2 uvicorn workers
- [ ] `/health` responds correctly from the container
- [ ] `migrations/` directory is present inside the container at `/app/migrations/`

---

## Testing

```bash
# Build
docker build -f Dockerfile.server -t nrev-lite-api:test .

# Run
docker run --rm -p 8000:8000 \
  -e DATABASE_URL=postgresql+asyncpg://nrev-lite:nrv@host.docker.internal:5432/nrv \
  -e REDIS_URL=redis://host.docker.internal:6379/0 \
  -e JWT_SECRET_KEY=test-secret-key-at-least-32-chars \
  nrev-lite-api:test

# Verify workers
# Logs should show: "Started server process" twice (one per worker)

# Verify migrations exist
docker run --rm nrev-lite-api:test ls /app/migrations/
```
