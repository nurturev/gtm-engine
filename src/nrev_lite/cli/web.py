"""Web commands: Google search, web scraping, content extraction."""

from __future__ import annotations

import sys
from typing import Any

import click

from nrev_lite.client.http import NrvApiError, NrvClient
from nrev_lite.utils.display import (
    print_error,
    print_json,
    print_success,
    print_table,
    print_warning,
    spinner,
)


def _require_auth() -> None:
    from nrev_lite.client.auth import is_authenticated

    if not is_authenticated():
        print_error("Not logged in. Run: nrev-lite auth login")
        sys.exit(1)


@click.group("web")
def web() -> None:
    """Google search, web scraping, and content extraction.

    \b
    Search:  nrev-lite web search "query"        (RapidAPI Google Search)
    Scrape:  nrev-lite web scrape <url>           (Parallel.ai Extract)
    Crawl:   nrev-lite web crawl <url>            (Parallel.ai Extract, multi-URL)
    Extract: nrev-lite web extract <url> --prompt (Parallel.ai Task API)
    """


# ---- Google Search (RapidAPI Real-Time Web Search) --------------------------


@web.command("search")
@click.argument("query")
@click.option("--num", default=10, help="Number of results (1-300).")
@click.option("--country", "gl", default=None, help="Country code (us, in, gb).")
@click.option("--time", "tbs", default=None,
              help="Time filter: hour, day, week, month, year.")
@click.option("--site", default=None,
              help="Restrict to a domain (e.g. linkedin.com).")
@click.option("--json-output", is_flag=True, help="Output raw JSON.")
def search_web(
    query: str,
    num: int,
    gl: str | None,
    tbs: str | None,
    site: str | None,
    json_output: bool,
) -> None:
    """Google search for GTM intelligence.

    \b
    Powered by RapidAPI Real-Time Web Search. Supports Google operators:
    site:, filetype:, inurl:, intitle:, -keyword

    \b
    Examples:
        nrev-lite websearch "Acme Corp funding"
        nrev-lite websearch "SaaS pricing" --site g2.com
        nrev-lite websearch "Mindtickle hiring" --time week
        nrev-lite websearch "AI startups" --country in --num 50
        nrev-lite websearch "site:linkedin.com/in CTO fintech"
    """
    _require_auth()

    params: dict[str, Any] = {"q": query, "num": num}
    if gl:
        params["gl"] = gl
    if tbs:
        params["time"] = tbs  # provider maps friendly names to tbs values
    if site:
        params["site"] = site

    client = NrvClient()
    try:
        with spinner(f'Searching Google: "{query}"'):
            result = client.execute("search_web", params, providers=["rapidapi_google"])
    except NrvApiError as exc:
        print_error(f"Search failed: {exc.message}")
        sys.exit(1)

    data = result.get("result") or result.get("data") or result

    if json_output:
        print_json(data)
        return

    _display_web_results(data)


@web.command("bulk-search")
@click.argument("queries", nargs=-1, required=True)
@click.option("--num", default=10, help="Results per query.")
@click.option("--country", "gl", default=None, help="Country code.")
@click.option("--json-output", is_flag=True, help="Output raw JSON.")
def bulk_search(
    queries: tuple[str, ...],
    num: int,
    gl: str | None,
    json_output: bool,
) -> None:
    """Run multiple Google searches concurrently.

    \b
    Examples:
        nrev-lite webbulk-search "Acme funding" "Acme hiring" "Acme reviews"
        nrev-lite webbulk-search "competitor A pricing" "competitor B pricing"
    """
    _require_auth()

    params: dict[str, Any] = {
        "queries": list(queries),
        "num": num,
    }
    if gl:
        params["gl"] = gl

    client = NrvClient()
    try:
        with spinner(f"Searching {len(queries)} queries..."):
            result = client.execute("search_web", params, providers=["rapidapi_google"])
    except NrvApiError as exc:
        print_error(f"Bulk search failed: {exc.message}")
        sys.exit(1)

    data = result.get("result") or result.get("data") or result

    if json_output:
        print_json(data)
        return

    # Display each query's results
    searches = data.get("searches", [data])
    for search_result in searches:
        _display_web_results(search_result)
        click.echo()


# ---- Web Scraping (Parallel Web Systems — parallel.ai) ----------------------


@web.command("scrape")
@click.argument("url")
@click.option("--objective", default=None,
              help="Focus extraction on this intent (e.g. 'pricing information').")
@click.option("--full-content", is_flag=True,
              help="Return full page content (not just excerpts).")
@click.option("--json-output", is_flag=True, help="Output raw JSON.")
def scrape_page(
    url: str,
    objective: str | None,
    full_content: bool,
    json_output: bool,
) -> None:
    """Scrape a webpage via Parallel.ai and get clean content.

    \b
    Returns markdown excerpts by default, or full content with --full-content.
    Handles JavaScript-rendered pages and PDFs automatically.

    \b
    Examples:
        nrev-lite webscrape https://acme.com/about
        nrev-lite webscrape https://acme.com/pricing --objective "pricing tiers"
        nrev-lite webscrape https://competitor.com/pricing --full-content --json-output
    """
    _require_auth()

    params: dict[str, Any] = {"url": url}
    if objective:
        params["objective"] = objective
    if full_content:
        params["full_content"] = True

    client = NrvClient()
    try:
        with spinner(f"Scraping {url}..."):
            result = client.execute("scrape_page", params, providers=["parallel_web"])
    except NrvApiError as exc:
        print_error(f"Scrape failed: {exc.message}")
        sys.exit(1)

    data = result.get("result") or result.get("data") or result

    if json_output:
        print_json(data)
        return

    _display_extract_result(data)


@web.command("crawl")
@click.argument("urls", nargs=-1, required=True)
@click.option("--objective", default=None,
              help="Focus extraction on this intent.")
@click.option("--full-content", is_flag=True,
              help="Return full page content.")
@click.option("--json-output", is_flag=True, help="Output raw JSON.")
def crawl_site(
    urls: tuple[str, ...],
    objective: str | None,
    full_content: bool,
    json_output: bool,
) -> None:
    """Extract content from multiple URLs via Parallel.ai.

    \b
    Pass multiple URLs (auto-batched in groups of 10 for optimal throughput).

    \b
    Examples:
        nrev-lite webcrawl https://acme.com/about https://acme.com/pricing
        nrev-lite webcrawl https://acme.com/team https://acme.com/careers \\
            --objective "leadership and hiring"
    """
    _require_auth()

    url_list = list(urls)
    click.echo(f"Extracting content from {len(url_list)} URLs")
    if len(url_list) > 10:
        cost_est = len(url_list)
        click.echo(f"Estimated cost: ~{cost_est} credits with platform key")
        if not click.confirm("Proceed?"):
            return

    params: dict[str, Any] = {"urls": url_list}
    if objective:
        params["objective"] = objective
    if full_content:
        params["full_content"] = True

    client = NrvClient()
    try:
        with spinner(f"Extracting {len(url_list)} URLs..."):
            result = client.execute("scrape_page", params, providers=["parallel_web"])
    except NrvApiError as exc:
        print_error(f"Crawl failed: {exc.message}")
        sys.exit(1)

    data = result.get("result") or result.get("data") or result

    if json_output:
        print_json(data)
        return

    _display_extract_result(data)


@web.command("extract")
@click.argument("url")
@click.option("--prompt", "input_prompt", required=True,
              help="What to extract (natural language instruction).")
@click.option("--processor", default="base",
              type=click.Choice(["lite", "base", "core", "pro"]),
              help="Processing tier (default: base).")
@click.option("--json-output", is_flag=True, help="Output raw JSON.")
def extract_structured(
    url: str,
    input_prompt: str,
    processor: str,
    json_output: bool,
) -> None:
    """Extract structured data from a page using AI (Parallel Task API).

    \b
    Uses Parallel.ai's Task API for LLM-powered structured extraction.

    \b
    Examples:
        nrev-lite webextract https://acme.com/pricing \\
            --prompt "Extract pricing tiers with name, price, and features"
        nrev-lite webextract https://acme.com/team \\
            --prompt "List all team members with name, title, and LinkedIn URL"
        nrev-lite webextract https://acme.com/about \\
            --prompt "Company description, founding year, employee count, HQ location" \\
            --processor core
    """
    _require_auth()

    params: dict[str, Any] = {
        "input": f"Extract from {url}: {input_prompt}",
        "processor": processor,
    }

    client = NrvClient()
    try:
        with spinner(f"Extracting from {url} (processor={processor})..."):
            result = client.execute("extract_structured", params, providers=["parallel_web"])
    except NrvApiError as exc:
        print_error(f"Extraction failed: {exc.message}")
        sys.exit(1)

    data = result.get("result") or result.get("data") or result

    if json_output:
        print_json(data)
        return

    # Display task output
    output = data.get("output", data)
    if isinstance(output, dict) or isinstance(output, list):
        print_success("Extracted data:")
        print_json(output)
    elif isinstance(output, str):
        print_success("Extracted data:")
        click.echo(output)
    else:
        print_json(data)

    # Show citations if available
    basis = data.get("basis", [])
    if basis:
        click.echo()
        click.secho("  Sources:", fg="yellow")
        for b in basis[:5]:
            if isinstance(b, dict):
                click.echo(f"    • {b.get('url', b.get('source', str(b)))}")


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def _display_web_results(data: dict[str, Any]) -> None:
    """Display Google web search results."""
    results = data.get("results", [])
    if not results:
        print_warning("No results found.")
        return

    query = data.get("query", "")
    click.echo(f'\nGoogle: "{query}" — {len(results)} results\n')

    for r in results:
        pos = r.get("position", "")
        title = r.get("title", "")
        url = r.get("url", "")
        snippet = r.get("snippet", "")
        date = r.get("date", "")

        click.secho(f"  {pos}. {title}", fg="bright_white", bold=True)
        click.secho(f"     {url}", fg="cyan")
        if date:
            click.secho(f"     {date}", fg="yellow")
        if snippet:
            click.echo(f"     {snippet[:200]}")
        click.echo()

    # Knowledge graph
    kg = data.get("knowledge_graph")
    if kg:
        click.secho("  Knowledge Graph:", fg="yellow", bold=True)
        if isinstance(kg, dict):
            click.echo(f"    {kg.get('title', '')} ({kg.get('type', '')})")
            if kg.get("description"):
                click.echo(f"    {kg['description'][:200]}")
        click.echo()

    # Related searches
    related = data.get("related_searches", [])
    if related:
        click.secho("  Related searches:", fg="yellow")
        for q in related[:5]:
            if isinstance(q, str):
                click.echo(f"    • {q}")
            elif isinstance(q, dict):
                click.echo(f"    • {q.get('query', str(q))}")


def _display_extract_result(data: dict[str, Any]) -> None:
    """Display Parallel.ai extract results."""
    pages = data.get("pages", [])
    errors = data.get("errors", [])

    if not pages and not errors:
        # Single page result
        content = data.get("content") or data.get("full_content") or data.get("excerpts")
        if content:
            title = data.get("title", "Untitled")
            url = data.get("url", "")
            click.secho(f"\n  {title}", fg="bright_white", bold=True)
            click.secho(f"  {url}", fg="cyan")
            click.echo()
            if isinstance(content, list):
                click.echo("\n".join(content))
            elif isinstance(content, str):
                if len(content) > 3000:
                    click.echo(content[:3000])
                    click.echo(f"\n  ... truncated ({len(content)} total chars). Use --json-output for full.")
                else:
                    click.echo(content)
            return
        print_json(data)
        return

    click.echo(f"\nExtracted {len(pages)} pages:\n")

    for p in pages:
        title = p.get("title", "Untitled")
        url = p.get("url", "")
        word_count = p.get("word_count", 0)

        click.secho(f"  {title}", fg="bright_white", bold=True)
        click.secho(f"  {url}", fg="cyan")
        if word_count:
            click.echo(f"  {word_count} words")

        content = p.get("content") or p.get("full_content")
        if content:
            preview = content[:500] if isinstance(content, str) else str(content)[:500]
            click.echo(f"  {preview}...")
        click.echo()

    if errors:
        click.secho(f"\n  {len(errors)} URLs failed:", fg="red")
        for e in errors:
            click.echo(f"    • {e.get('url', 'unknown')}: {e.get('error_type', e.get('content', 'unknown'))}")
