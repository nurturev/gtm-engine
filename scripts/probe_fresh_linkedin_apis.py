"""Probe Fresh LinkedIn Profile Data endpoints and save sample responses.

Covers the 8 endpoints in scope for the Phase-2 grooming:
- Company profile by LinkedIn URL
- Company profile by domain
- Get person/profile posts
- Get company posts
- Get post reactions
- Get post comments
- Get post reposts
- (one extra TBD — may map to a shares / engagement variant)

The API key is read from random_script.py-equivalent literal for the grooming
probe only; rotate after this run.

Saves each response to docs/sample_responses/<slug>.json with a small header
block capturing the endpoint path and request params used.
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
from typing import Any

import requests

API_KEY = "ebe379537emsh25d4f1c35a4548ap102a82jsn3d058755d911"
HOST = "fresh-linkedin-profile-data.p.rapidapi.com"
BASE = f"https://{HOST}"

HEADERS = {
    "x-rapidapi-host": HOST,
    "x-rapidapi-key": API_KEY,
    "Content-Type": "application/json",
}

OUT = pathlib.Path(__file__).resolve().parent.parent / "docs" / "sample_responses"
OUT.mkdir(parents=True, exist_ok=True)

# Seed values — public pages to probe against.
SAMPLE_PROFILE_URL = "https://www.linkedin.com/in/mohnishkewlani/"
SAMPLE_COMPANY_URL = "https://www.linkedin.com/company/google/"
SAMPLE_DOMAIN = "google.com"
# Fallback URNs from LinkedIn / Fresh LinkedIn docs. Real URNs will be
# harvested from the posts responses and re-probed below.
SAMPLE_POST_URN = "7159120870928625666"


def save(slug: str, meta: dict[str, Any], payload: Any) -> pathlib.Path:
    path = OUT / f"{slug}.json"
    envelope = {"__probe_meta__": meta, "response": payload}
    path.write_text(json.dumps(envelope, indent=2, default=str))
    return path


def probe_get(slug: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
    url = f"{BASE}{path}"
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=60)
    except requests.RequestException as exc:
        print(f"[{slug}] {path} → EXC {exc}")
        return {"status": "exception", "error": str(exc)}

    meta = {
        "slug": slug,
        "method": "GET",
        "path": path,
        "status_code": resp.status_code,
        "params": params,
    }

    try:
        payload = resp.json()
    except ValueError:
        payload = {"__text_body__": resp.text[:2000]}

    saved = save(slug, meta, payload)
    size = len(json.dumps(payload))
    print(f"[{slug}] {path} status={resp.status_code} bytes={size} → {saved.name}")
    return {"status_code": resp.status_code, "payload": payload, "saved": str(saved)}


def probe_post(slug: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    url = f"{BASE}{path}"
    try:
        resp = requests.post(url, headers=HEADERS, json=body, timeout=60)
    except requests.RequestException as exc:
        print(f"[{slug}] {path} → EXC {exc}")
        return {"status": "exception", "error": str(exc)}

    meta = {
        "slug": slug,
        "method": "POST",
        "path": path,
        "status_code": resp.status_code,
        "body": body,
    }

    try:
        payload = resp.json()
    except ValueError:
        payload = {"__text_body__": resp.text[:2000]}

    saved = save(slug, meta, payload)
    size = len(json.dumps(payload))
    print(f"[{slug}] POST {path} status={resp.status_code} bytes={size} → {saved.name}")
    return {"status_code": resp.status_code, "payload": payload, "saved": str(saved)}


def try_paths(slug: str, paths: list[str], params: dict[str, Any]) -> dict[str, Any] | None:
    """Try a list of candidate paths; return the first 2xx, else save the last 4xx."""
    last = None
    for path in paths:
        res = probe_get(slug, path, params)
        last = res
        code = res.get("status_code")
        if code and 200 <= code < 300:
            return res
        # small delay to avoid burst-triggering rate limits
        time.sleep(0.3)
    return last


def main() -> int:
    print(f"Saving to {OUT}")
    print("=" * 60)

    # Phase 1 — company profile (two endpoints)
    company_by_url = try_paths(
        "company_by_url",
        [
            "/get-company-by-linkedinurl",
            "/get-company-by-url",
            "/enrich-company",
            "/company-profile-by-url",
            "/get-linkedin-company",
        ],
        {"linkedin_url": SAMPLE_COMPANY_URL},
    )

    company_by_domain = try_paths(
        "company_by_domain",
        [
            "/get-company-by-domain",
            "/company-by-domain",
            "/get-linkedin-company-by-domain",
            "/enrich-company-by-domain",
        ],
        {"domain": SAMPLE_DOMAIN},
    )

    # Phase 2 — posts (by profile, by company)
    profile_posts = try_paths(
        "profile_posts",
        [
            "/get-profile-posts",
            "/get-person-posts",
            "/get-linkedin-profile-posts",
            "/profile-posts",
        ],
        {"linkedin_url": SAMPLE_PROFILE_URL},
    )

    company_posts = try_paths(
        "company_posts",
        [
            "/get-company-posts",
            "/company-posts",
            "/get-linkedin-company-posts",
        ],
        {"linkedin_url": SAMPLE_COMPANY_URL},
    )

    # Harvest URN candidates from posts responses
    urn_candidates: list[str] = [SAMPLE_POST_URN]

    def _scan_for_urns(blob: Any) -> None:
        if isinstance(blob, dict):
            for k, v in blob.items():
                if isinstance(v, str) and ("urn:li:" in v or k.lower() in {"urn", "post_urn", "activity_urn", "share_urn"}):
                    urn_candidates.append(v)
                _scan_for_urns(v)
        elif isinstance(blob, list):
            for item in blob:
                _scan_for_urns(item)

    for res in (profile_posts, company_posts):
        if res and res.get("payload"):
            _scan_for_urns(res["payload"])

    # De-dup preserving order
    seen: set[str] = set()
    urns = [u for u in urn_candidates if not (u in seen or seen.add(u))]
    print(f"URN candidates collected: {urns[:5]}")

    probe_urn = urns[0] if urns else SAMPLE_POST_URN
    print(f"Using probe URN: {probe_urn}")

    # Phase 3 — post engagement (reactions, comments, reposts)
    reactions = try_paths(
        "post_reactions",
        [
            "/get-post-reactions",
            "/get-reactions",
            "/post-reactions",
        ],
        {"urn": probe_urn},
    )

    comments = try_paths(
        "post_comments",
        [
            "/get-post-comments",
            "/get-comments",
            "/post-comments",
        ],
        {"urn": probe_urn},
    )

    reposts = try_paths(
        "post_reposts",
        [
            "/get-post-reposts",
            "/get-reposts",
            "/post-reposts",
            "/get-shares",
        ],
        {"urn": probe_urn},
    )

    # Phase 4 — any remaining variants the user might have meant
    # (shares / people who engaged / a second posts variant)
    try_paths(
        "post_reactions_alt",
        ["/get-reactions-from-post"],
        {"urn": probe_urn},
    )

    # Summary
    print("=" * 60)
    summary = {
        "company_by_url": company_by_url and company_by_url.get("status_code"),
        "company_by_domain": company_by_domain and company_by_domain.get("status_code"),
        "profile_posts": profile_posts and profile_posts.get("status_code"),
        "company_posts": company_posts and company_posts.get("status_code"),
        "post_reactions": reactions and reactions.get("status_code"),
        "post_comments": comments and comments.get("status_code"),
        "post_reposts": reposts and reposts.get("status_code"),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
