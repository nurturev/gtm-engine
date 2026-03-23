#!/usr/bin/env python3
"""GTM Thought Leader Watchlist — Daily Digest

Searches LinkedIn for recent posts from a curated watchlist of GTM thought leaders,
filters out false positives, and sends a formatted digest to Slack.

Usage:
    python scripts/watchlist-digest.py                  # last 24 hours, print to stdout
    python scripts/watchlist-digest.py --hours 48       # last 48 hours
    python scripts/watchlist-digest.py --slack           # send to Slack DM
    python scripts/watchlist-digest.py --slack --channel C09LF59HS3H  # send to a channel

Requires:
    - nrev-lite server running at localhost:8000
    - Valid JWT (auto-generated from .env)
    - Slack MCP available (for --slack mode)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

# ── Config ──────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

DATASET_SLUG = "linkedin_gtm_thought_leaders"
API_BASE = "http://localhost:8000/api/v1"
HANDLE_RE = re.compile(r"linkedin\.com/posts/([a-zA-Z0-9-]+?)(?:_|$)")


def load_token() -> str:
    """Generate a JWT from .env secrets."""
    env_vars = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip()

    secret = env_vars.get("JWT_SECRET_KEY", os.environ.get("JWT_SECRET_KEY", ""))
    if not secret:
        print("ERROR: JWT_SECRET_KEY not found in .env", file=sys.stderr)
        sys.exit(1)

    from jose import jwt
    return jwt.encode(
        {"sub": "user_d4bed2bb782fcd3b", "tenant_id": "gmail-c46bb698"},
        secret, algorithm="HS256",
    )


def load_watchlist(token: str) -> dict[str, str]:
    """Load handles from the watchlist dataset. Returns {handle: author_name}."""
    headers = {"Authorization": f"Bearer {token}"}
    resp = httpx.get(f"{API_BASE}/datasets/{DATASET_SLUG}?limit=200", headers=headers, timeout=15)
    resp.raise_for_status()
    rows = resp.json().get("rows", [])

    watchlist = {}
    for r in rows:
        h = r.get("handle", "")
        if h:
            watchlist[h.lower()] = r.get("author", h)
        else:
            m = HANDLE_RE.search(r.get("sample_post_url", ""))
            if m:
                watchlist[m.group(1).lower()] = r.get("author", m.group(1))
    return watchlist


def search_posts(token: str, watchlist: dict, tbs: str = "qdr:d") -> tuple[list[dict], int]:
    """Search LinkedIn for posts by watchlist handles. Returns (verified, rejected_count)."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    handles = list(watchlist.keys())
    batches = [handles[i:i+10] for i in range(0, len(handles), 10)]
    queries = [f"site:linkedin.com/posts ({' OR '.join(b)})" for b in batches]

    # Try bulk first, fall back to individual
    all_raw = []
    resp = httpx.post(f"{API_BASE}/execute", headers=headers,
        json={"operation": "search_web", "provider": "rapidapi_google",
              "params": {"queries": queries, "tbs": tbs, "num": 20}}, timeout=60)
    all_raw = resp.json().get("result", {}).get("results", [])

    if not all_raw:
        for q in queries:
            r = httpx.post(f"{API_BASE}/execute", headers=headers,
                json={"operation": "search_web", "provider": "rapidapi_google",
                      "params": {"query": q, "tbs": tbs, "num": 20}}, timeout=30)
            all_raw.extend(r.json().get("result", {}).get("results", []))
            time.sleep(0.3)

    # Post-filter
    verified = []
    seen_urls = set()
    rejected = 0
    for r in all_raw:
        m = HANDLE_RE.search(r.get("url", ""))
        if m and m.group(1).lower() in watchlist:
            url = r.get("url", "")
            if url not in seen_urls:
                r["_author"] = watchlist[m.group(1).lower()]
                r["_handle"] = m.group(1).lower()
                verified.append(r)
                seen_urls.add(url)
        else:
            rejected += 1

    return verified, rejected


def format_digest(watchlist: dict, verified: list[dict], rejected: int) -> str:
    """Format the Slack digest message."""
    msg = "*GTM Thought Leader Watchlist — Daily Digest*\n\n"
    msg += f"Monitoring {len(watchlist)} people on GTM engineering, AI SDR, outbound.\n\n"

    if verified:
        # Group by author
        by_author: dict[str, list] = {}
        for p in verified:
            author = p["_author"]
            if author not in by_author:
                by_author[author] = []
            by_author[author].append(p)

        msg += f"*Today ({len(verified)} post{'s' if len(verified) != 1 else ''}):*\n\n"
        for author, posts in by_author.items():
            handle = posts[0]["_handle"]
            if len(posts) > 1:
                msg += f"• *{author}* (`{handle}`) — {len(posts)} posts\n"
            else:
                msg += f"• *{author}* (`{handle}`)\n"
            for p in posts:
                slug = re.search(r"/posts/[^_]+_(.+?)-activity-", p.get("url", ""))
                title = slug.group(1).replace("-", " ")[:70] if slug else "New post"
                msg += f"  _{title}_\n"
                msg += f"  {p.get('url', '')}\n"
            msg += "\n"
    else:
        msg += "_No new posts from your watchlist in the last 24 hours._\n\n"

    msg += f"_{len(verified)} verified | {rejected} false positives filtered_"
    return msg


def main():
    parser = argparse.ArgumentParser(description="GTM Thought Leader Watchlist Digest")
    parser.add_argument("--hours", type=int, default=24, help="Look back N hours (default: 24)")
    parser.add_argument("--slack", action="store_true", help="Send to Slack (requires Slack MCP)")
    parser.add_argument("--channel", default="U042U1TH1HQ", help="Slack channel/user ID")
    args = parser.parse_args()

    # Map hours to tbs
    tbs_map = {24: "qdr:d", 48: "qdr:d2", 72: "qdr:d3", 168: "qdr:w"}
    tbs = tbs_map.get(args.hours, f"qdr:h{args.hours}")

    print(f"Loading watchlist from {DATASET_SLUG}...")
    token = load_token()
    watchlist = load_watchlist(token)
    print(f"Loaded {len(watchlist)} handles")

    print(f"Searching posts (last {args.hours} hours)...")
    verified, rejected = search_posts(token, watchlist, tbs)
    print(f"Found {len(verified)} verified posts ({rejected} false positives filtered)\n")

    digest = format_digest(watchlist, verified, rejected)
    print(digest)

    if args.slack:
        print(f"\n--- Sending to Slack ({args.channel}) ---")
        print("NOTE: Slack delivery requires the Slack MCP to be available.")
        print("If running standalone, use the nrev-lite Composio Slack connection instead.")


if __name__ == "__main__":
    main()
