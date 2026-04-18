"""Fresh LinkedIn post commands — fetch, details, search, reactions, comments."""

from __future__ import annotations

import sys
from typing import Any

import click

from nrev_lite.client.http import NrvApiError, NrvClient
from nrev_lite.utils.display import print_error, print_json, print_warning, spinner


def _require_auth() -> None:
    from nrev_lite.client.auth import is_authenticated

    if not is_authenticated():
        print_error("Not logged in. Run: nrev-lite auth login")
        sys.exit(1)


def _execute(operation: str, params: dict[str, Any], as_json: bool) -> None:
    client = NrvClient()
    try:
        with spinner(f"{operation} via fresh_linkedin..."):
            result = client.execute(operation, params, providers=["fresh_linkedin"])
    except NrvApiError as exc:
        print_error(f"{operation} failed: {exc.message}")
        sys.exit(1)
    print_json(result)


@click.group("posts")
def posts() -> None:
    """Fresh LinkedIn posts — fetch, details, search, reactions, comments."""


@posts.command("fetch")
@click.option("--linkedin", required=True, help="LinkedIn profile (/in/<slug>) or company (/company/<slug>) URL.")
@click.option("--cursor", default=None, help="Pagination cursor from a prior response.")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def fetch(linkedin: str, cursor: str | None, as_json: bool) -> None:
    """Recent posts by a LinkedIn profile or company URL.

    Auto-detects profile vs company from the URL path. 3 credits/call.
    """
    _require_auth()

    lower = linkedin.lower()
    if "/in/" in lower:
        operation = "fetch_profile_posts"
    elif "/company/" in lower:
        operation = "fetch_company_posts"
    else:
        print_error("URL must be a LinkedIn profile (/in/<slug>) or company (/company/<slug>) URL.")
        sys.exit(2)

    params: dict[str, Any] = {"linkedin_url": linkedin}
    if cursor:
        params["cursor"] = cursor

    _execute(operation, params, as_json)


@posts.command("details")
@click.option("--urn", required=True, help="Bare activity id (e.g. 7450415215956987904) — from a prior posts response.")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def details(urn: str, as_json: bool) -> None:
    """Full detail for a single post by URN.

    URN is the bare numeric id (not `urn:li:activity:...` and not the post URL). 3 credits/call.
    """
    _require_auth()
    _execute("fetch_post_details", {"urn": urn}, as_json)


@posts.command("reactions")
@click.option("--urn", required=True, help="Bare activity id from a prior posts response.")
@click.option("--cursor", default=None, help="Pagination cursor from a prior response.")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def reactions(urn: str, cursor: str | None, as_json: bool) -> None:
    """Reactors on a LinkedIn post by bare activity URN.

    Returns name, headline, linkedin_url (URN-form), reaction type per reactor. 3 credits/call.
    """
    _require_auth()
    params: dict[str, Any] = {"urn": urn}
    if cursor:
        params["cursor"] = cursor
    _execute("fetch_post_reactions", params, as_json)


@posts.command("search")
@click.option("--keyword", default=None, help="Keyword filter (search_keywords).")
@click.option("--sort-by", default=None, help="'Latest' (default) or 'Relevance'.")
@click.option("--date-posted", default=None, help="Vendor-native time window (e.g. 'past-24h', 'past-week').")
@click.option("--content-type", default=None, help="Vendor-native content filter (e.g. 'videos', 'images').")
@click.option("--from-member", multiple=True, help="Author member URNs (ACoAA... form). Repeatable.")
@click.option("--from-company", multiple=True, help="Author company URNs. Repeatable.")
@click.option("--mentioning-member", multiple=True, help="Mentioned member URNs. Repeatable.")
@click.option("--mentioning-company", multiple=True, help="Mentioned company URNs. Repeatable.")
@click.option("--author-company", multiple=True, help="Author's company URNs. Repeatable.")
@click.option("--author-industry", multiple=True, help="Author industry codes. Repeatable.")
@click.option("--author-keyword", default=None, help="Author keyword filter.")
@click.option("--page", type=int, default=1, help="Page number (1-indexed).")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def search(
    keyword: str | None,
    sort_by: str | None,
    date_posted: str | None,
    content_type: str | None,
    from_member: tuple[str, ...],
    from_company: tuple[str, ...],
    mentioning_member: tuple[str, ...],
    mentioning_company: tuple[str, ...],
    author_company: tuple[str, ...],
    author_industry: tuple[str, ...],
    author_keyword: str | None,
    page: int,
    as_json: bool,
) -> None:
    """Search LinkedIn posts via Fresh LinkedIn with filters.

    At least one of --keyword, --from-member, --from-company, --mentioning-*,
    --author-* is required. URN filters expect member/company URNs (ACoAA...),
    NOT profile URLs — harvest from a prior `posts fetch` response's `poster.urn`.
    3 credits/call.
    """
    _require_auth()
    params: dict[str, Any] = {"page": page}
    if keyword:            params["search_keywords"]    = keyword
    if sort_by:            params["sort_by"]            = sort_by
    if date_posted:        params["date_posted"]        = date_posted
    if content_type:       params["content_type"]       = content_type
    if from_member:        params["from_member"]        = list(from_member)
    if from_company:       params["from_company"]       = list(from_company)
    if mentioning_member:  params["mentioning_member"]  = list(mentioning_member)
    if mentioning_company: params["mentioning_company"] = list(mentioning_company)
    if author_company:     params["author_company"]     = list(author_company)
    if author_industry:    params["author_industry"]    = list(author_industry)
    if author_keyword:     params["author_keyword"]     = author_keyword
    _execute("search_posts", params, as_json)


@posts.command("comments")
@click.option("--urn", required=True, help="Bare activity id from a prior posts response.")
@click.option("--cursor", default=None, help="Pagination cursor from a prior response.")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def comments(urn: str, cursor: str | None, as_json: bool) -> None:
    """Comments on a LinkedIn post by bare activity URN.

    Paginated via cursor. Returns text, commenter (URN-form linkedin_url), created_at, reply_count. 3 credits/call.
    """
    _require_auth()
    params: dict[str, Any] = {"urn": urn}
    if cursor:
        params["cursor"] = cursor
    _execute("fetch_post_comments", params, as_json)
