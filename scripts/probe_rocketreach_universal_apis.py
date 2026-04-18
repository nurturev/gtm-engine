"""Probe RocketReach Universal API endpoints and save sample responses.

Context
-------
We currently call the legacy v2 endpoints:
    GET  /api/v2/person/lookup
    POST /api/v2/person/search
    GET  /api/v2/company/lookup
    POST /api/v2/company/search

nRev has a high-volume vendor contract for the "Universal" variants instead:
    GET  /api/v2/universal/person/lookup
    POST /api/v2/universal/person/search
    GET  /api/v2/universal/company/lookup
    POST /api/v2/universal/company/search

Goal of this probe
------------------
Capture raw request/response pairs for BOTH endpoint families side-by-side so
we can diff the vendor contract and plan client/server changes. We do not
touch any server code — this is pure exploration.

Auth
----
Same as the current implementation: header `Api-Key: <key>`. The key is read
from the `ROCKETREACH_API` env var so it never lands in source.

Output
------
Each response is written to `docs/rocketreach_sample_responses/<slug>.json`
with a small meta header capturing the method, path, status, and the exact
request params/body used.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time
from typing import Any

import requests


API_KEY = os.environ.get("ROCKETREACH_API")
if not API_KEY:
    # Best-effort fallback: read from the repo root .env if python-dotenv is
    # not installed in the shell that's invoking this.
    env_path = pathlib.Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ROCKETREACH_API="):
                API_KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
                break

if not API_KEY:
    print("ROCKETREACH_API env var not set — aborting.", file=sys.stderr)
    sys.exit(1)


BASE = "https://api.rocketreach.co"
HEADERS = {
    "Api-Key": API_KEY,
    "Content-Type": "application/json",
    "Accept": "application/json",
}

OUT = pathlib.Path(__file__).resolve().parent.parent / "docs" / "rocketreach_sample_responses"
OUT.mkdir(parents=True, exist_ok=True)


# Seeds — chosen so each probe lands on a well-known public profile/company.
SAMPLE_PERSON_LINKEDIN = "https://www.linkedin.com/in/satyanadella/"
SAMPLE_PERSON_NAME = "Satya Nadella"
SAMPLE_PERSON_EMPLOYER = "Microsoft"
SAMPLE_COMPANY_DOMAIN = "microsoft.com"
SAMPLE_COMPANY_NAME = "Microsoft"
SAMPLE_COMPANY_LINKEDIN = "https://www.linkedin.com/company/microsoft/"

# Filters for the search endpoints — deliberately broad so we get multiple
# rows back and can inspect the list shape.
PERSON_SEARCH_QUERY = {
    "current_employer": ["Microsoft"],
}
COMPANY_SEARCH_QUERY = {
    "name": ["Stripe"],
}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def save(slug: str, meta: dict[str, Any], payload: Any) -> pathlib.Path:
    path = OUT / f"{slug}.json"
    envelope = {"__probe_meta__": meta, "response": payload}
    path.write_text(json.dumps(envelope, indent=2, default=str))
    return path


def _parse(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return {"__text_body__": resp.text[:2000]}


def probe_get(slug: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
    url = f"{BASE}{path}"
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=60)
    except requests.RequestException as exc:
        print(f"[{slug}] GET {path} → EXC {exc}")
        return {"status": "exception", "error": str(exc)}

    payload = _parse(resp)
    meta = {
        "slug": slug,
        "method": "GET",
        "url": url,
        "path": path,
        "status_code": resp.status_code,
        "request_params": params,
        "response_headers": {
            k: v for k, v in resp.headers.items()
            if k.lower() in {"rr-request-id", "retry-after", "x-ratelimit-limit", "x-ratelimit-remaining"}
        },
    }
    saved = save(slug, meta, payload)
    size = len(json.dumps(payload, default=str))
    print(f"[{slug}] GET {path} → {resp.status_code} ({size}B) → {saved.name}")
    return {"status_code": resp.status_code, "payload": payload, "saved": str(saved)}


def probe_post(slug: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    url = f"{BASE}{path}"
    try:
        resp = requests.post(url, headers=HEADERS, json=body, timeout=60)
    except requests.RequestException as exc:
        print(f"[{slug}] POST {path} → EXC {exc}")
        return {"status": "exception", "error": str(exc)}

    payload = _parse(resp)
    meta = {
        "slug": slug,
        "method": "POST",
        "url": url,
        "path": path,
        "status_code": resp.status_code,
        "request_body": body,
        "response_headers": {
            k: v for k, v in resp.headers.items()
            if k.lower() in {"rr-request-id", "retry-after", "x-ratelimit-limit", "x-ratelimit-remaining"}
        },
    }
    saved = save(slug, meta, payload)
    size = len(json.dumps(payload, default=str))
    print(f"[{slug}] POST {path} → {resp.status_code} ({size}B) → {saved.name}")
    return {"status_code": resp.status_code, "payload": payload, "saved": str(saved)}


# ---------------------------------------------------------------------------
# Probe plan
# ---------------------------------------------------------------------------


def main() -> int:
    print(f"Saving to {OUT}")
    print("=" * 72)

    # Sanity: the /account endpoint is shared between v1/v2/universal and tells
    # us the key is valid before we burn credits.
    probe_get("account", "/api/v2/account", {})

    # ------------------------------------------------------------------ #
    # Phase A — Person LOOKUP: universal vs legacy v2                    #
    # ------------------------------------------------------------------ #
    print("\n--- Phase A — Person lookup ---")

    # A1. Legacy v2 by LinkedIn URL (most accurate identifier)
    v2_person_linkedin = probe_get(
        "v2_person_lookup_linkedin",
        "/api/v2/person/lookup",
        {"linkedin_url": SAMPLE_PERSON_LINKEDIN},
    )
    time.sleep(0.3)

    # A2. Universal person lookup by LinkedIn URL
    uni_person_linkedin = probe_get(
        "universal_person_lookup_linkedin",
        "/api/v2/universal/person/lookup",
        {"linkedin_url": SAMPLE_PERSON_LINKEDIN},
    )
    time.sleep(0.3)

    # A3. Universal person lookup by name + employer — exercises the
    # identifier-combo path.
    uni_person_by_name = probe_get(
        "universal_person_lookup_name_employer",
        "/api/v2/universal/person/lookup",
        {"name": SAMPLE_PERSON_NAME, "current_employer": SAMPLE_PERSON_EMPLOYER},
    )
    time.sleep(0.3)

    # A4. Universal person lookup with explicit reveal_* flags — this is the
    # main behavioural change vs v2 (fine-grained credit control).
    uni_person_with_reveals = probe_get(
        "universal_person_lookup_with_reveals",
        "/api/v2/universal/person/lookup",
        {
            "linkedin_url": SAMPLE_PERSON_LINKEDIN,
            "reveal_professional_email": "true",
            "reveal_personal_email": "true",
            "reveal_phone": "true",
        },
    )
    time.sleep(0.3)

    # A5. If either universal lookup came back with status=searching/progress,
    # poll the check_status endpoint so we capture the async completion shape.
    async_ids: list[int] = []
    for res in (uni_person_linkedin, uni_person_by_name, uni_person_with_reveals):
        p = res.get("payload") or {}
        if isinstance(p, dict) and p.get("status") in {"searching", "progress", "waiting"}:
            if isinstance(p.get("id"), int):
                async_ids.append(p["id"])

    if async_ids:
        print(f"Polling check_status for ids={async_ids} (async lookup completion)")
        for attempt in range(6):  # ~30s total
            time.sleep(5)
            res = probe_get(
                f"universal_person_check_status_attempt{attempt + 1}",
                "/api/v2/universal/person/checkStatus",
                {"ids": ",".join(str(i) for i in async_ids)},
            )
            payload = res.get("payload") or {}
            # Payload may be a list OR a dict with a list. Bail once all done.
            entries = payload if isinstance(payload, list) else payload.get("profiles", [])
            if entries and all(
                isinstance(e, dict) and e.get("status") in {"complete", "failed"}
                for e in entries
            ):
                break
    else:
        print("No async lookups returned — skipping check_status polling.")

    # ------------------------------------------------------------------ #
    # Phase B — Person SEARCH: universal vs legacy v2                    #
    # ------------------------------------------------------------------ #
    print("\n--- Phase B — Person search ---")

    v2_person_search = probe_post(
        "v2_person_search",
        "/api/v2/person/search",
        {"query": PERSON_SEARCH_QUERY, "page_size": 10, "start": 1},
    )
    time.sleep(0.3)

    uni_person_search = probe_post(
        "universal_person_search",
        "/api/v2/universal/person/search",
        {"query": PERSON_SEARCH_QUERY, "page_size": 10, "start": 1},
    )
    time.sleep(0.3)

    # Harvest a person ID from search results to feed back into the lookup
    # endpoint — verifies the id-based lookup path.
    lookup_id: int | None = None
    for res in (uni_person_search, v2_person_search):
        p = res.get("payload") or {}
        profiles = p.get("profiles") if isinstance(p, dict) else None
        if isinstance(profiles, list) and profiles:
            first = profiles[0]
            if isinstance(first, dict) and isinstance(first.get("id"), int):
                lookup_id = first["id"]
                break

    if lookup_id is not None:
        probe_get(
            "universal_person_lookup_by_id",
            "/api/v2/universal/person/lookup",
            {"id": lookup_id},
        )
        time.sleep(0.3)
    else:
        print("No id harvested from search — skipping id-based lookup.")

    # ------------------------------------------------------------------ #
    # Phase C — Company LOOKUP: universal vs legacy v2                   #
    # ------------------------------------------------------------------ #
    print("\n--- Phase C — Company lookup ---")

    probe_get(
        "v2_company_lookup_domain",
        "/api/v2/company/lookup",
        {"domain": SAMPLE_COMPANY_DOMAIN},
    )
    time.sleep(0.3)

    probe_get(
        "universal_company_lookup_domain",
        "/api/v2/universal/company/lookup",
        {"domain": SAMPLE_COMPANY_DOMAIN},
    )
    time.sleep(0.3)

    probe_get(
        "universal_company_lookup_name",
        "/api/v2/universal/company/lookup",
        {"name": SAMPLE_COMPANY_NAME},
    )
    time.sleep(0.3)

    probe_get(
        "universal_company_lookup_linkedin",
        "/api/v2/universal/company/lookup",
        {"linkedin_url": SAMPLE_COMPANY_LINKEDIN},
    )
    time.sleep(0.3)

    # ------------------------------------------------------------------ #
    # Phase D — Company SEARCH: universal vs legacy v2                   #
    # ------------------------------------------------------------------ #
    print("\n--- Phase D — Company search ---")

    probe_post(
        "v2_company_search",
        "/api/v2/searchCompany",
        {"query": COMPANY_SEARCH_QUERY, "page_size": 10, "start": 1},
    )
    time.sleep(0.3)

    probe_post(
        "universal_company_search",
        "/api/v2/universal/company/search",
        {"query": COMPANY_SEARCH_QUERY, "page_size": 10, "start": 1},
    )
    time.sleep(0.3)

    # ------------------------------------------------------------------ #
    # Summary                                                            #
    # ------------------------------------------------------------------ #
    print("\n" + "=" * 72)
    print("Probe complete. Inspect docs/rocketreach_sample_responses/ for shapes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
