"""Standalone health check for every Parallel Web endpoint the server uses.

Mirrors the exact HTTP calls made by server/execution/providers/parallel_web.py
so we can verify — independent of the nrev-lite stack — which endpoints are
actually reachable and which are failing.

Run:
    python scripts/test_parallel_endpoints.py

Reads PARALLEL_KEY from the environment (or from the project's .env).
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

PARALLEL_BASE = "https://api.parallel.ai"
REQUEST_TIMEOUT = 60.0
POLL_INTERVAL = 5.0
TASK_POLL_TIMEOUT = 180.0
GROUP_POLL_TIMEOUT = 300.0


def _load_key() -> str:
    key = os.environ.get("PARALLEL_KEY")
    if key:
        return key
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("PARALLEL_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    print("ERROR: PARALLEL_KEY not found in env or .env", file=sys.stderr)
    sys.exit(1)


API_KEY = _load_key()


def _xkey_headers() -> dict[str, str]:
    return {"x-api-key": API_KEY, "Content-Type": "application/json"}


def _bearer_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}


class Report:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def add(
        self,
        name: str,
        method: str,
        path: str,
        status: int | None,
        ok: bool,
        elapsed_ms: int,
        note: str = "",
    ) -> None:
        self.rows.append({
            "name": name,
            "method": method,
            "path": path,
            "status": status,
            "ok": ok,
            "elapsed_ms": elapsed_ms,
            "note": note,
        })

    def print(self) -> None:
        print()
        print("=" * 100)
        print(f"{'ENDPOINT':<40} {'METHOD':<6} {'STATUS':<8} {'OK':<6} {'MS':<8} NOTE")
        print("-" * 100)
        for r in self.rows:
            ok = "PASS" if r["ok"] else "FAIL"
            print(
                f"{r['name']:<40} {r['method']:<6} {str(r['status']):<8} "
                f"{ok:<6} {r['elapsed_ms']:<8} {r['note']}"
            )
        print("=" * 100)
        passed = sum(1 for r in self.rows if r["ok"])
        print(f"Total: {len(self.rows)} | Passed: {passed} | Failed: {len(self.rows) - passed}")
        print()


report = Report()


async def _timed(
    name: str,
    method: str,
    path: str,
    coro,
) -> tuple[int | None, Any]:
    start = time.time()
    try:
        resp = await coro
        elapsed = int((time.time() - start) * 1000)
        ok = 200 <= resp.status_code < 300
        note = ""
        if not ok:
            try:
                err_body = resp.json()
                note = str(err_body)[:200]
            except Exception:
                note = resp.text[:200]
        report.add(name, method, path, resp.status_code, ok, elapsed, note)
        return resp.status_code, resp
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        report.add(name, method, path, None, False, elapsed, f"EXC: {type(e).__name__}: {e}")
        return None, None


# ----------------------------------------------------------------------
# 1. POST /v1beta/search
# ----------------------------------------------------------------------
async def test_search(client: httpx.AsyncClient) -> None:
    body = {
        "objective": "What is the capital of France?",
        "search_queries": ["capital of France"],
        "mode": "fast",
        "max_results": 3,
    }
    await _timed(
        "search_web",
        "POST",
        "/v1beta/search",
        client.post(f"{PARALLEL_BASE}/v1beta/search", headers=_xkey_headers(), json=body),
    )


# ----------------------------------------------------------------------
# 2. POST /v1beta/extract
# ----------------------------------------------------------------------
async def test_extract(client: httpx.AsyncClient) -> None:
    body = {
        "urls": ["https://www.example.com"],
        "excerpts": True,
    }
    await _timed(
        "scrape_page / extract",
        "POST",
        "/v1beta/extract",
        client.post(f"{PARALLEL_BASE}/v1beta/extract", headers=_xkey_headers(), json=body),
    )


# ----------------------------------------------------------------------
# 3 + 4. POST /v1/tasks/runs  →  GET /v1/tasks/runs/{id}/result
# ----------------------------------------------------------------------
async def test_task_run_and_poll(client: httpx.AsyncClient) -> None:
    create_body = {
        "input": "What is the capital of France? Respond with just the city name.",
        "processor": "base",
    }
    status, resp = await _timed(
        "task create (extract_structured)",
        "POST",
        "/v1/tasks/runs",
        client.post(f"{PARALLEL_BASE}/v1/tasks/runs", headers=_xkey_headers(), json=create_body),
    )
    if not resp or status is None or not (200 <= status < 300):
        report.add(
            "task poll result",
            "GET",
            "/v1/tasks/runs/{id}/result",
            None,
            False,
            0,
            "SKIPPED — task create failed",
        )
        return

    run_id = resp.json().get("run_id")
    if not run_id:
        report.add(
            "task poll result",
            "GET",
            "/v1/tasks/runs/{id}/result",
            None,
            False,
            0,
            "SKIPPED — no run_id in create response",
        )
        return

    # Note: the server's current poll code uses the same client timeout for GETs,
    # but 202/408 responses can take a long time. Use a dedicated client with a
    # shorter per-request timeout so we surface the actual behavior.
    poll_url = f"{PARALLEL_BASE}/v1/tasks/runs/{run_id}/result"
    final_status: int | None = None
    final_ok = False
    note = ""
    start = time.time()
    async with httpx.AsyncClient(timeout=30.0) as poll_client:
        while (time.time() - start) < TASK_POLL_TIMEOUT:
            try:
                r = await poll_client.get(poll_url, headers=_xkey_headers())
            except Exception as e:
                note = f"EXC during poll: {type(e).__name__}: {e}"
                # Keep trying rather than abort; network blips are recoverable.
                await asyncio.sleep(POLL_INTERVAL)
                continue
            final_status = r.status_code
            if r.status_code == 200:
                data = r.json()
                # Print raw response once for debugging
                if not hasattr(test_task_run_and_poll, "_printed"):
                    print(f"[DEBUG task poll raw keys: {list(data.keys())}]")
                    print(f"[DEBUG task poll sample: {str(data)[:300]}]")
                    test_task_run_and_poll._printed = True
                st = data.get("status") or data.get("run", {}).get("status", "unknown")
                if st == "completed":
                    final_ok = True
                    note = "completed"
                    break
                if st == "failed":
                    note = f"task failed: {data.get('errors')}"
                    break
                note = f"status={st}"
            elif r.status_code in (202, 408):
                note = f"still running (HTTP {r.status_code})"
            else:
                try:
                    note = str(r.json())[:200]
                except Exception:
                    note = r.text[:200]
                break
            await asyncio.sleep(POLL_INTERVAL)

    if not final_ok and not note:
        note = f"timed out after {TASK_POLL_TIMEOUT}s"
    report.add(
        "task poll result",
        "GET",
        "/v1/tasks/runs/{id}/result",
        final_status,
        final_ok,
        int((time.time() - start) * 1000),
        note,
    )


# ----------------------------------------------------------------------
# 5, 6, 7, 8. Task Groups
# ----------------------------------------------------------------------
async def test_task_groups(client: httpx.AsyncClient) -> None:
    # 5. Create group
    status, resp = await _timed(
        "taskgroup create",
        "POST",
        "/v1beta/tasks/groups",
        client.post(f"{PARALLEL_BASE}/v1beta/tasks/groups", headers=_xkey_headers(), json={}),
    )
    if not resp or status != 200:
        for name, method, path in [
            ("taskgroup add runs", "POST", "/v1beta/tasks/groups/{id}/runs"),
            ("taskgroup status", "GET", "/v1beta/tasks/groups/{id}"),
            ("taskgroup list runs", "GET", "/v1beta/tasks/groups/{id}/runs"),
        ]:
            report.add(name, method, path, None, False, 0, "SKIPPED — group create failed")
        return

    group_id = resp.json().get("taskgroup_id")
    if not group_id:
        for name, method, path in [
            ("taskgroup add runs", "POST", "/v1beta/tasks/groups/{id}/runs"),
            ("taskgroup status", "GET", "/v1beta/tasks/groups/{id}"),
            ("taskgroup list runs", "GET", "/v1beta/tasks/groups/{id}/runs"),
        ]:
            report.add(name, method, path, None, False, 0, "SKIPPED — no taskgroup_id")
        return

    # 6. Add runs — try the server's current body shape first, then the shape
    #    the API error message suggests, so we can tell which is right.
    runs_body_server_shape = {
        "runs": [
            {"input": "Capital of France? One word.", "processor": "base"},
            {"input": "Capital of Japan? One word.", "processor": "base"},
        ],
    }
    await _timed(
        "taskgroup add runs (shape: {'runs':[...]})",
        "POST",
        "/v1beta/tasks/groups/{id}/runs",
        client.post(
            f"{PARALLEL_BASE}/v1beta/tasks/groups/{group_id}/runs",
            headers=_xkey_headers(),
            json=runs_body_server_shape,
        ),
    )

    runs_body_inputs_shape = {
        "inputs": [
            {"input": "Capital of France? One word.", "processor": "base"},
            {"input": "Capital of Japan? One word.", "processor": "base"},
        ],
    }
    await _timed(
        "taskgroup add runs (shape: {'inputs':[...]})",
        "POST",
        "/v1beta/tasks/groups/{id}/runs",
        client.post(
            f"{PARALLEL_BASE}/v1beta/tasks/groups/{group_id}/runs",
            headers=_xkey_headers(),
            json=runs_body_inputs_shape,
        ),
    )

    # 7. Status poll (one-shot, don't wait for completion)
    await _timed(
        "taskgroup status",
        "GET",
        "/v1beta/tasks/groups/{id}",
        client.get(
            f"{PARALLEL_BASE}/v1beta/tasks/groups/{group_id}",
            headers=_xkey_headers(),
        ),
    )

    # 8. List runs (include_output=true like the server)
    await _timed(
        "taskgroup list runs",
        "GET",
        "/v1beta/tasks/groups/{id}/runs",
        client.get(
            f"{PARALLEL_BASE}/v1beta/tasks/groups/{group_id}/runs",
            headers=_xkey_headers(),
            params={"include_output": "true"},
        ),
    )


# ----------------------------------------------------------------------
# 9. POST /v1beta/chat/completions (Bearer auth)
# ----------------------------------------------------------------------
async def test_chat(client: httpx.AsyncClient) -> None:
    body = {
        "model": "base",
        "messages": [{"role": "user", "content": "What is the capital of France? One word."}],
    }
    await _timed(
        "chat_research",
        "POST",
        "/v1beta/chat/completions",
        client.post(
            f"{PARALLEL_BASE}/v1beta/chat/completions",
            headers=_bearer_headers(),
            json=body,
        ),
    )


# ----------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------
async def main() -> None:
    print(f"Testing Parallel Web endpoints with key ...{API_KEY[-4:]}")
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        await test_search(client)
        await test_extract(client)
        await test_task_run_and_poll(client)
        await test_task_groups(client)
        await test_chat(client)
    report.print()


if __name__ == "__main__":
    asyncio.run(main())
