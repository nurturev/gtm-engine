#!/usr/bin/env python3
"""End-to-end smoke tests for the Fresh LinkedIn CLI surface.

Runs every new/changed command against the server selected by the `TARGET`
constant at the top of this file (`"local"` or `"staging"`) and reports
PASS / FAIL / SKIP. For commands whose inputs depend on a prior response
(pagination values, URNs for details/reactions/comments, member/company URNs
for search), values are harvested live from earlier calls instead of
hardcoded so the whole suite reflects actually-working server data.

Scenarios exercised (per backend-api-testing-blueprint §3):

Enrichment
  • `enrich person --provider fresh_linkedin`                 → 200 + profile
  • `enrich company --linkedin <company>`                     → 200 + company
  • `enrich company --domain <domain>`                        → 200 + company

Profile posts
  • `posts fetch --linkedin <profile>`                        → 200 + page 1 + pagination sub-dict
  • `posts fetch --start X --pagination-token Y` (pair)       → 200 + page 2 distinct from page 1
  • `posts fetch --start X` without --pagination-token        → 400 pairing-rule violation, no debit
  • `posts fetch --type posts` filter pass-through            → 200

Company posts
  • `posts fetch --linkedin <company>`                        → 200 + pagination sub-dict
  • `posts fetch --linkedin <company> --sort-by top`          → 200, filter pass-through
  • `posts fetch --start X --pagination-token Y` (pair)       → 200 + page 2 (company)
  • `posts fetch --linkedin <company> --pagination-token Y`   → 400 pairing-rule violation

Post details (no pagination — vendor design)
  • `posts details --urn <live>`                              → 200 + single post

Post reactions (single-value page, no token)
  • `posts reactions --urn <live>`                            → 200 + reactions[]
  • `posts reactions --urn <live> --page 2 --type ALL`        → 200, filter pass-through
  • `posts reactions --urn <live> --page abc`                 → 400 numeric-coercion violation

Post comments (paired page + pagination_token)
  • `posts comments --urn <live>`                             → 200 + comments[]
  • `posts comments --urn <live> --page X --pagination-token Y` → 200 + page 2 distinct
  • `posts comments --urn <live> --page X` without --pagination-token → 400

Search posts (unchanged input; envelope moves page under pagination)
  • `posts search --keyword ...`                              → 200 + posts[]
  • `posts search --from-member <URN>`                        → 200
  • `posts search --from-company <URN> --keyword hiring`      → 200
  • `posts search --mentioning-member <URN> --page 2`         → 200
  • `posts search --author-industry 4 --author-keyword founder` → 200

Release gate (HLD §15): the pagination round-trip branches must run and
return a distinct page-2 envelope before the phase is called complete.

Usage:
    ~/.venvs/nrev-dev/bin/python scripts/test_fresh_linkedin_cli.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any


# Flip this one variable to switch between local dev server and staging.
#   "local"   → http://localhost:8000       (home: ~/.nrev-lite-dev)
#   "staging" → staging API on nurturev.com (home: ~/.nrev-lite-staging)
TARGET = "local"

_TARGETS: dict[str, dict[str, str]] = {
    "local": {
        "api_url": "http://localhost:8000",
        "home": os.path.expanduser("~/.nrev-lite-dev"),
    },
    "staging": {
        "api_url": "https://nrev-lite-api.public.staging.nurturev.com",
        "home": os.path.expanduser("~/.nrev-lite-staging"),
    },
}
if TARGET not in _TARGETS:
    raise SystemExit(f"Invalid TARGET={TARGET!r}; expected one of {list(_TARGETS)}")
_CONF = _TARGETS[TARGET]

NREV_BIN = os.path.expanduser("~/.venvs/nrev-dev/bin/nrev-lite")
ENV: dict[str, str] = {
    **os.environ,
    "NREV_LITE_HOME": _CONF["home"],
    "NREV_API_URL": _CONF["api_url"],
    "NREV_PLATFORM_URL": os.environ.get("NREV_PLATFORM_URL", "http://localhost:3000"),
    # Keep Rich output flat so stdout is parseable. COLUMNS=10000 prevents
    # Rich from line-wrapping long strings (e.g. post URLs) inside JSON output,
    # which would otherwise inject newlines into string literals.
    "NO_COLOR": "1",
    "TERM": "dumb",
    "COLUMNS": "10000",
    "LINES": "10000",
}

PROFILE_URL = "https://www.linkedin.com/in/williamhgates/"
COMPANY_URL = "https://www.linkedin.com/company/microsoft/"
DOMAIN = "microsoft.com"
FALLBACK_URN = "7450415215956987904"

PER_CALL_TIMEOUT = 90
ANSI_RE = __import__("re").compile(r"\x1b\[[0-9;]*m")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class Result:
    name: str
    status: str  # PASS | FAIL | SKIP
    duration: float
    note: str = ""
    details: str = ""  # stderr/snippet for debugging


RESULTS: list[Result] = []


def _strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def run(cmd: list[str], timeout: int = PER_CALL_TIMEOUT) -> tuple[int, str, str, float]:
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            env=ENV,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr, time.monotonic() - t0
    except subprocess.TimeoutExpired:
        return 124, "", f"TIMEOUT after {timeout}s", time.monotonic() - t0


def _find_json_block(stdout: str) -> dict | None:
    """Extract the first top-level JSON object from stdout (Rich may add noise)."""
    stdout = _strip_ansi(stdout)
    depth = 0
    start = -1
    for i, ch in enumerate(stdout):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                chunk = stdout[start : i + 1]
                try:
                    return json.loads(chunk)
                except Exception:
                    start = -1
    return None


def _harvest(path_list: list[str], resp: dict | None) -> Any:
    """Walk nested keys; return None if any step misses."""
    cur: Any = resp
    for key in path_list:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def record(
    name: str, code: int, stdout: str, stderr: str, duration: float, note: str = ""
) -> dict | None:
    """Evaluate a command result, record it, and return parsed JSON (if any)."""
    flat = _strip_ansi(stdout + stderr)
    failed_markers = ("failed:", "Error", "Traceback", "404 Not Found")
    is_fail = code != 0 or any(
        m in flat for m in failed_markers if m != "Error" or "Error: " in flat
    )

    resp = _find_json_block(stdout)

    status = "FAIL" if is_fail else "PASS"
    snippet = ""
    if is_fail:
        snippet = (stderr.strip() or flat.strip())[:300]

    RESULTS.append(Result(name, status, duration, note, snippet))
    _print_row(RESULTS[-1])
    return resp


def record_expect_fail(
    name: str,
    code: int,
    stdout: str,
    stderr: str,
    duration: float,
    must_contain: str,
    note: str = "",
) -> None:
    """Inverse of `record` — PASS iff the CLI exited non-zero AND the combined
    stdout/stderr contains `must_contain` (case-insensitive). Used for the
    pairing-rule / numeric-coercion negative paths, where a clean 400 from the
    server is the success criterion."""
    # Rich soft-wraps long error lines at ~COLUMNS, so a phrase like
    # "non-negative integer" can land across a line break. Collapse all
    # whitespace before matching so wrap position doesn't matter.
    flat_raw = _strip_ansi(stdout + stderr).lower()
    flat = " ".join(flat_raw.split())
    needle = " ".join(must_contain.lower().split())
    expected = code != 0 and needle in flat
    status = "PASS" if expected else "FAIL"
    snippet = ""
    if not expected:
        if code == 0:
            snippet = f"CLI exited 0; expected failure containing '{must_contain}'"
        else:
            snippet = f"missing expected marker '{must_contain}' in output"
    RESULTS.append(Result(name, status, duration, note, snippet))
    _print_row(RESULTS[-1])


def skip(name: str, why: str) -> None:
    RESULTS.append(Result(name, "SKIP", 0.0, why))
    _print_row(RESULTS[-1])


def _print_row(r: Result) -> None:
    color = {"PASS": "\033[32m", "FAIL": "\033[31m", "SKIP": "\033[33m"}[r.status]
    reset = "\033[0m"
    dur = f"{r.duration:5.2f}s" if r.duration else "  -  "
    note = f"  [{r.note}]" if r.note else ""
    print(f"  {color}{r.status:4}{reset}  {dur}  {r.name}{note}")
    if r.status == "FAIL" and r.details:
        first_line = r.details.splitlines()[0] if r.details else ""
        print(f"         \033[31m↳\033[0m {first_line[:180]}")


def _section(title: str) -> None:
    print(f"\n\033[1m── {title} ──\033[0m")


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def preflight() -> bool:
    print("\033[1mFresh LinkedIn CLI smoke test\033[0m")
    print(f"  target:  {TARGET}")
    print(f"  binary:  {NREV_BIN}")
    print(f"  API:     {ENV['NREV_API_URL']}")
    print(f"  HOME:    {ENV['NREV_LITE_HOME']}")

    if not os.path.exists(NREV_BIN):
        print(f"\n\033[31mFATAL\033[0m: nrev-lite binary not found at {NREV_BIN}")
        return False

    # Confirm the resolved URL really matches TARGET (catches a stale config override)
    expected_host = ENV["NREV_API_URL"].split("://", 1)[-1].rstrip("/")
    code, out, err, _ = run([NREV_BIN, "status"], timeout=15)
    flat = _strip_ansi(out + err)
    if expected_host not in flat:
        print(f"\n\033[31mFATAL\033[0m: status does not show {expected_host}")
        print(f"  → check {ENV['NREV_LITE_HOME']}/config.toml for a stale server.url")
        print(flat[:600])
        return False
    if "online" not in flat:
        if TARGET == "local":
            print("\n\033[31mFATAL\033[0m: local server not online. Start it with:")
            print("    cd server && uvicorn server.app:app --reload")
        else:
            print(
                f"\n\033[31mFATAL\033[0m: {TARGET} server not reachable at {expected_host}"
            )
        return False
    print(f"  status:  \033[32mok\033[0m ({expected_host} online)\n")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    if not preflight():
        return 2

    # -------- 1. Person enrichment --------
    _section("1. Person enrichment (--provider fresh_linkedin)")
    code, out, err, d = run(
        [
            NREV_BIN,
            "enrich",
            "person",
            "--linkedin",
            PROFILE_URL,
            "--provider",
            "fresh_linkedin",
        ]
    )
    record("enrich person --linkedin (fresh_linkedin)", code, out, err, d)

    # -------- 2. Company enrichment --------
    _section("2. Company enrichment (--provider fresh_linkedin)")
    code, out, err, d = run(
        [
            NREV_BIN,
            "enrich",
            "company",
            "--linkedin",
            COMPANY_URL,
            "--provider",
            "fresh_linkedin",
        ]
    )
    record("enrich company --linkedin (fresh_linkedin)", code, out, err, d)

    code, out, err, d = run(
        [
            NREV_BIN,
            "enrich",
            "company",
            "--domain",
            DOMAIN,
            "--provider",
            "fresh_linkedin",
        ]
    )
    record("enrich company --domain (fresh_linkedin)", code, out, err, d)

    # -------- 3. Posts group --------
    _section("3a. posts fetch — profile")
    code, out, err, d = run(
        [
            NREV_BIN,
            "posts",
            "fetch",
            "--linkedin",
            PROFILE_URL,
            "--json",
        ]
    )
    profile_resp = record("posts fetch --linkedin <profile>", code, out, err, d)

    posts = _harvest(["result", "posts"], profile_resp) or []
    profile_start = _harvest(["result", "pagination", "start"], profile_resp)
    profile_token = _harvest(["result", "pagination", "pagination_token"], profile_resp)
    profile_first_urn = posts[0].get("urn") if posts else None
    profile_post_urn = profile_first_urn
    profile_poster_urn = posts[0].get("poster", {}).get("urn") if posts else None

    # Filter pass-through — --type on a profile URL routes to the vendor's
    # profile-posts filter (HLD §5.1). Pass-through only; no enum validation.
    code, out, err, d = run(
        [
            NREV_BIN,
            "posts",
            "fetch",
            "--linkedin",
            PROFILE_URL,
            "--type",
            "posts",
            "--json",
        ]
    )
    record("posts fetch --type posts (filter pass-through)", code, out, err, d)

    _section("3b. posts fetch — company")
    code, out, err, d = run(
        [
            NREV_BIN,
            "posts",
            "fetch",
            "--linkedin",
            COMPANY_URL,
            "--json",
        ]
    )
    company_resp = record("posts fetch --linkedin <company>", code, out, err, d)

    company_posts_list = _harvest(["result", "posts"], company_resp) or []
    company_start = _harvest(["result", "pagination", "start"], company_resp)
    company_token = _harvest(["result", "pagination", "pagination_token"], company_resp)
    company_poster_urn = (
        company_posts_list[0].get("poster", {}).get("urn")
        if company_posts_list
        else None
    )

    # Filter pass-through — --sort-by on a company URL routes to vendor
    # sort_by (HLD §5.2). Also confirms envelope pagination still populated.
    code, out, err, d = run(
        [
            NREV_BIN,
            "posts",
            "fetch",
            "--linkedin",
            COMPANY_URL,
            "--sort-by",
            "top",
            "--json",
        ]
    )
    record("posts fetch --sort-by top (company filter pass-through)", code, out, err, d)

    _section("3c. posts fetch — pagination round-trip (profile)")
    if profile_start and profile_token:
        code, out, err, d = run(
            [
                NREV_BIN,
                "posts",
                "fetch",
                "--linkedin",
                PROFILE_URL,
                "--start",
                str(profile_start),
                "--pagination-token",
                str(profile_token),
                "--json",
            ]
        )
        page2_resp = record(
            "posts fetch --start --pagination-token",
            code,
            out,
            err,
            d,
            note=f"start={profile_start} token={str(profile_token)[:14]}…",
        )
        # Release-gate signal (HLD §15): page 2 must be distinct from page 1.
        page2_posts = _harvest(["result", "posts"], page2_resp) or []
        page2_first_urn = page2_posts[0].get("urn") if page2_posts else None
        if (
            page2_first_urn
            and profile_first_urn
            and page2_first_urn != profile_first_urn
        ):
            record(
                "posts fetch page 2 distinct from page 1 (release gate)",
                0,
                "",
                "",
                0.0,
                note=f"p1={profile_first_urn[:14]}… p2={page2_first_urn[:14]}…",
            )
        else:
            skip(
                "posts fetch page 2 distinct from page 1 (release gate)",
                "page 2 returned empty or identical first post to page 1",
            )
    else:
        skip(
            "posts fetch --start --pagination-token",
            "no pagination.start / pagination.pagination_token in page 1",
        )

    # Pairing-rule violation — --start without --pagination-token → expect 400.
    code, out, err, d = run(
        [
            NREV_BIN,
            "posts",
            "fetch",
            "--linkedin",
            PROFILE_URL,
            "--start",
            "10",
            "--json",
        ]
    )
    record_expect_fail(
        "posts fetch --start alone rejected (pairing rule)",
        code,
        out,
        err,
        d,
        must_contain="provided together",
    )

    _section("3c². posts fetch — pagination round-trip (company)")
    if company_start and company_token:
        code, out, err, d = run(
            [
                NREV_BIN,
                "posts",
                "fetch",
                "--linkedin",
                COMPANY_URL,
                "--start",
                str(company_start),
                "--pagination-token",
                str(company_token),
                "--json",
            ]
        )
        record(
            "posts fetch --start --pagination-token (company)",
            code,
            out,
            err,
            d,
            note=f"start={company_start} token={str(company_token)[:14]}…",
        )
    else:
        skip(
            "posts fetch --start --pagination-token (company)",
            "no pagination.start / pagination.pagination_token in company page 1",
        )

    # Company-side pairing-rule violation — --pagination-token alone → expect 400.
    code, out, err, d = run(
        [
            NREV_BIN,
            "posts",
            "fetch",
            "--linkedin",
            COMPANY_URL,
            "--pagination-token",
            "fake-tok",
            "--json",
        ]
    )
    record_expect_fail(
        "posts fetch --pagination-token alone rejected (company pairing)",
        code,
        out,
        err,
        d,
        must_contain="provided together",
    )

    _section("3d. posts details")
    urn_for_details = profile_post_urn or FALLBACK_URN
    code, out, err, d = run(
        [
            NREV_BIN,
            "posts",
            "details",
            "--urn",
            urn_for_details,
            "--json",
        ]
    )
    record(
        "posts details --urn",
        code,
        out,
        err,
        d,
        note=f"urn={urn_for_details} ({'live' if profile_post_urn else 'fallback'})",
    )

    _section("3e. posts reactions")
    code, out, err, d = run(
        [
            NREV_BIN,
            "posts",
            "reactions",
            "--urn",
            urn_for_details,
            "--json",
        ]
    )
    reactions_resp = record(
        "posts reactions --urn",
        code,
        out,
        err,
        d,
        note=f"urn={urn_for_details}",
    )

    # Reactions page-2 with --type filter — single-value pagination (no token).
    # Vendor emits no next-page hint, so we just confirm page 2 doesn't error.
    code, out, err, d = run(
        [
            NREV_BIN,
            "posts",
            "reactions",
            "--urn",
            urn_for_details,
            "--page",
            "2",
            "--type",
            "ALL",
            "--json",
        ]
    )
    record(
        "posts reactions --page 2 --type ALL",
        code,
        out,
        err,
        d,
        note=f"urn={urn_for_details}",
    )

    # Numeric-coercion violation — non-numeric page → 400 before upstream.
    code, out, err, d = run(
        [
            NREV_BIN,
            "posts",
            "reactions",
            "--urn",
            urn_for_details,
            "--page",
            "abc",
            "--json",
        ]
    )
    record_expect_fail(
        "posts reactions --page abc rejected (numeric coercion)",
        code,
        out,
        err,
        d,
        must_contain="non-negative integer",
    )

    _section("3f. posts comments")
    code, out, err, d = run(
        [
            NREV_BIN,
            "posts",
            "comments",
            "--urn",
            urn_for_details,
            "--json",
        ]
    )
    comments_resp = record(
        "posts comments --urn",
        code,
        out,
        err,
        d,
        note=f"urn={urn_for_details}",
    )

    # Comments pagination — need both page and pagination_token together
    comments_page = _harvest(["result", "pagination", "page"], comments_resp)
    comments_token = _harvest(
        ["result", "pagination", "pagination_token"], comments_resp
    )
    if comments_page and comments_token:
        code, out, err, d = run(
            [
                NREV_BIN,
                "posts",
                "comments",
                "--urn",
                urn_for_details,
                "--page",
                str(comments_page),
                "--pagination-token",
                str(comments_token),
                "--json",
            ]
        )
        record(
            "posts comments --page --pagination-token",
            code,
            out,
            err,
            d,
            note=f"page={comments_page} token={str(comments_token)[:14]}…",
        )
    else:
        skip(
            "posts comments --page --pagination-token",
            "page + pagination_token not both returned by first comments call",
        )

    # Comments pairing-rule violation — --page alone → 400.
    code, out, err, d = run(
        [
            NREV_BIN,
            "posts",
            "comments",
            "--urn",
            urn_for_details,
            "--page",
            "2",
            "--json",
        ]
    )
    record_expect_fail(
        "posts comments --page alone rejected (pairing rule)",
        code,
        out,
        err,
        d,
        must_contain="provided together",
    )

    _section("3g. posts search")
    code, out, err, d = run(
        [
            NREV_BIN,
            "posts",
            "search",
            "--keyword",
            "AI agents",
            "--json",
        ]
    )
    record("posts search --keyword", code, out, err, d)

    # NOTE: vendor only accepts sort_by="Latest" (Relevance is documented in the
    # CLI help but rejected upstream as of 2026-04). Date/content-type values are
    # title-cased with spaces ("Past week", "Videos"), NOT kebab-case.
    code, out, err, d = run(
        [
            NREV_BIN,
            "posts",
            "search",
            "--keyword",
            "GTM",
            "--sort-by",
            "Latest",
            "--date-posted",
            "Past week",
            "--json",
        ]
    )
    record("posts search (Latest, Past week)", code, out, err, d)

    code, out, err, d = run(
        [
            NREV_BIN,
            "posts",
            "search",
            "--keyword",
            "AI",
            "--content-type",
            "Videos",
            "--json",
        ]
    )
    record("posts search --content-type Videos", code, out, err, d)

    if profile_poster_urn:
        code, out, err, d = run(
            [
                NREV_BIN,
                "posts",
                "search",
                "--from-member",
                profile_poster_urn,
                "--json",
            ]
        )
        record(
            "posts search --from-member",
            code,
            out,
            err,
            d,
            note=f"member={profile_poster_urn[:14]}…",
        )
    else:
        skip("posts search --from-member", "no poster.urn from profile fetch")

    if company_poster_urn:
        code, out, err, d = run(
            [
                NREV_BIN,
                "posts",
                "search",
                "--from-company",
                company_poster_urn,
                "--keyword",
                "hiring",
                "--json",
            ]
        )
        record(
            "posts search --from-company",
            code,
            out,
            err,
            d,
            note=f"company={company_poster_urn[:14]}…",
        )
    else:
        skip("posts search --from-company", "no company poster.urn from company fetch")

    if profile_poster_urn:
        code, out, err, d = run(
            [
                NREV_BIN,
                "posts",
                "search",
                "--mentioning-member",
                profile_poster_urn,
                "--page",
                "2",
                "--json",
            ]
        )
        record(
            "posts search --mentioning-member --page 2",
            code,
            out,
            err,
            d,
            note=f"member={profile_poster_urn[:14]}…",
        )
    else:
        skip("posts search --mentioning-member", "no poster.urn to reuse")

    code, out, err, d = run(
        [
            NREV_BIN,
            "posts",
            "search",
            "--author-industry",
            "4",
            "--author-keyword",
            "founder",
            "--json",
        ]
    )
    record("posts search --author-industry --author-keyword", code, out, err, d)

    # -------- Summary --------
    _summary()
    return 0 if all(r.status != "FAIL" for r in RESULTS) else 1


def _summary() -> None:
    print("\n" + "=" * 78)
    passed = sum(r.status == "PASS" for r in RESULTS)
    failed = sum(r.status == "FAIL" for r in RESULTS)
    skipped = sum(r.status == "SKIP" for r in RESULTS)
    total = len(RESULTS)
    total_dur = sum(r.duration for r in RESULTS)
    print(
        f"  \033[32m{passed} passed\033[0m, "
        f"\033[31m{failed} failed\033[0m, "
        f"\033[33m{skipped} skipped\033[0m "
        f"  ({total} total, {total_dur:.1f}s)"
    )
    if failed:
        print("\n  Failed tests:")
        for r in RESULTS:
            if r.status == "FAIL":
                first = (r.details.splitlines() or [""])[0][:200]
                print(f"    \033[31m✘\033[0m {r.name}")
                if first:
                    print(f"        {first}")
    print("=" * 78)


if __name__ == "__main__":
    sys.exit(main())
