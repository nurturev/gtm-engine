# Task 03: Cap Batch Size at 25 Records

**Status:** Not Started
**Priority:** P0 — Required for rolling restart safety
**Deployment Doc Reference:** Section 13 (V1 Accepted Constraints)

---

## Goal

Add a server-side hard cap of 25 records on the batch execution endpoint. This prevents long-running requests (>30 seconds) that would be killed during rolling restarts in EKS.

---

## Context

Batch execution runs synchronously within a single HTTP request. With 5 concurrency and ~1-2s per provider call:
- 25 records ≈ 10-15 seconds (safe within 30s grace window)
- 50 records ≈ 20-30 seconds (borderline)
- 100+ records ≈ 40+ seconds (will be killed during deploys)

V2 will introduce async job queue for larger batches.

---

## What to Change

In `server/execution/router.py`, add validation at the top of `execute_batch_endpoint()`:

```python
MAX_BATCH_SIZE_V1 = 25

if len(body.operations) > MAX_BATCH_SIZE_V1:
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Batch size {len(body.operations)} exceeds maximum of {MAX_BATCH_SIZE_V1} records. "
        f"Split into smaller batches or use nRev for large-scale operations."
    )
```

---

## Files to Modify

| File | Change |
|------|--------|
| `server/execution/router.py` | Add `MAX_BATCH_SIZE_V1 = 25` constant and validation in `execute_batch_endpoint()` |

---

## Acceptance Criteria

- [ ] Batch requests with >25 operations return 400 with clear error message
- [ ] Batch requests with <=25 operations work as before
- [ ] Error message suggests splitting into smaller batches
- [ ] Constant is defined at module level (not hardcoded inline)

---

## Testing

```bash
# Test with 25 records — should succeed
# Test with 26 records — should return 400

# Verify via pytest
pytest tests/ -k "batch" -v
```
