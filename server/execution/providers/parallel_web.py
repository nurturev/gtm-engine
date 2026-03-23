"""Parallel Web Systems (parallel.ai) — AI-native web intelligence provider.

Parallel provides purpose-built web APIs for AI agents: search, extract,
async tasks, batch task groups, chat, findall, and monitoring.

Supported operations:
    - search_web:           AI-powered web search with objectives
    - scrape_page:          Extract content from URLs (up to 10 per call)
    - extract_structured:   Task API for structured extraction (async)
    - batch_extract:        Task Groups for high-volume batch processing

API docs: https://docs.parallel.ai
Base URL: https://api.parallel.ai

Authentication: x-api-key header (stored as PARALLEL_KEY)

Rate limits:
    - Search:       600 req/min (POST only; GETs are free)
    - Extract:      600 req/min (POST only; max 10 URLs per request)
    - Task/Groups:  2,000 req/min (POST only; GET polling is free)
    - Chat:         300 req/min

Pricing:
    - Search:  $0.004/req (base), $0.009/req (pro)
    - Extract: Included in Web Tools tier
    - Task:    $5-$2,400 per 1K requests depending on processor
    - 20,000 free requests before paid pricing kicks in

Quirks:
    - Extract max 10 URLs per request — must batch larger sets
    - fetch_policy.max_age_seconds minimum is 600 (10 minutes)
    - search max_results is not guaranteed — may return fewer
    - GET requests (polling, status) do NOT count against rate limits
    - "processor" param on Search is DEPRECATED — use "mode" instead
    - Markdown output format for all text content
    - Handles JS-rendered pages and PDFs automatically
    - SOC-2 Type II certified
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from server.core.exceptions import ProviderError
from server.execution.providers.base import BaseProvider
from server.execution.providers import register_provider

logger = logging.getLogger(__name__)

PARALLEL_BASE = "https://api.parallel.ai"

# Concurrency control for bulk operations
_DEFAULT_CONCURRENCY = 20           # max parallel HTTP requests
_EXTRACT_BATCH_SIZE = 10            # Parallel caps at 10 URLs per extract call
_TASK_GROUP_BATCH_SIZE = 500        # recommended batch for task group runs
_POLL_INTERVAL = 5.0                # seconds between status polls
_POLL_TIMEOUT = 300.0               # 5 min default poll timeout
_REQUEST_TIMEOUT = 60.0             # per-request timeout


class ParallelWebProvider(BaseProvider):
    """Parallel Web Systems provider for web intelligence at scale.

    Designed for high-volume usage with built-in concurrency control,
    automatic URL batching, and async task processing.
    """

    name = "parallel_web"
    supported_operations = [
        "search_web",
        "scrape_page",
        "crawl_site",
        "extract_structured",
        "batch_extract",
        "chat_research",
    ]

    def _headers(self, api_key: str) -> dict[str, str]:
        return {
            "x-api-key": api_key,
            "Content-Type": "application/json",
        }

    async def execute(
        self,
        operation: str,
        params: dict[str, Any],
        api_key: str,
    ) -> dict[str, Any]:
        """Execute a Parallel Web operation."""
        dispatch = {
            "search_web": self._search,
            "scrape_page": self._extract,
            "crawl_site": self._extract,       # same API, different framing
            "extract_structured": self._task_run,
            "batch_extract": self._task_group,
            "chat_research": self._chat,
        }
        handler = dispatch.get(operation)
        if not handler:
            raise ProviderError(self.name, f"Unknown operation: {operation}")
        return await handler(params, api_key)

    async def health_check(self, api_key: str) -> bool:
        """Check if Parallel API is reachable and key is valid."""
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{PARALLEL_BASE}/v1beta/search",
                    headers=self._headers(api_key),
                    json={
                        "search_queries": ["test"],
                        "max_results": 1,
                        "mode": "fast",
                    },
                )
                return resp.status_code == 200
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Search API — POST /v1beta/search
    # ------------------------------------------------------------------

    async def _search(
        self, params: dict[str, Any], api_key: str
    ) -> dict[str, Any]:
        """AI-powered web search with natural language objectives.

        Params:
            q / query / objective (str):    Search intent (natural language, max 5K chars).
            search_queries (list[str]):     Keyword queries (max 200 chars each).
                                            At least one of objective or search_queries required.
            mode (str):                     "fast", "one-shot", or "agentic". Default: "one-shot".
            max_results (int):              Upper bound on results (max 20). Default: 10.
            include_domains (list[str]):    Only include results from these domains.
            exclude_domains (list[str]):    Exclude results from these domains.
            after_date (str):               Only results after this date (YYYY-MM-DD).
            max_chars_per_result (int):     Max excerpt chars per result (min 1000).
            max_chars_total (int):          Max total excerpt chars (min 1000).
        """
        # Build the request body
        body: dict[str, Any] = {}

        # Handle query/objective
        objective = params.get("objective") or params.get("q") or params.get("query")
        if objective:
            body["objective"] = str(objective)[:5000]

        search_queries = params.get("search_queries")
        if search_queries:
            if isinstance(search_queries, str):
                search_queries = [search_queries]
            body["search_queries"] = [str(q)[:200] for q in search_queries]

        # If user only passed q, also add it as a search query for better results
        if objective and not search_queries:
            body["search_queries"] = [str(objective)[:200]]

        if not body.get("objective") and not body.get("search_queries"):
            raise ProviderError(self.name, "Missing required parameter: q or objective")

        # Mode
        mode = params.get("mode", "one-shot")
        if mode in ("fast", "one-shot", "agentic"):
            body["mode"] = mode

        # Results limit
        max_results = min(int(params.get("max_results", 10)), 20)
        body["max_results"] = max_results

        # Source policy
        source_policy: dict[str, Any] = {}
        if params.get("include_domains"):
            domains = params["include_domains"]
            if isinstance(domains, str):
                domains = [d.strip() for d in domains.split(",")]
            source_policy["include_domains"] = domains
        if params.get("exclude_domains"):
            domains = params["exclude_domains"]
            if isinstance(domains, str):
                domains = [d.strip() for d in domains.split(",")]
            source_policy["exclude_domains"] = domains
        if params.get("after_date"):
            source_policy["after_date"] = params["after_date"]
        if source_policy:
            body["source_policy"] = source_policy

        # Excerpt settings
        excerpts: dict[str, Any] = {}
        if params.get("max_chars_per_result"):
            excerpts["max_chars_per_result"] = max(1000, int(params["max_chars_per_result"]))
        if params.get("max_chars_total"):
            excerpts["max_chars_total"] = max(1000, int(params["max_chars_total"]))
        if excerpts:
            body["excerpts"] = excerpts

        # Fetch policy
        if params.get("max_age_seconds"):
            body["fetch_policy"] = {
                "max_age_seconds": max(600, int(params["max_age_seconds"])),
            }

        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.post(
                f"{PARALLEL_BASE}/v1beta/search",
                headers=self._headers(api_key),
                json=body,
            )

        self._check_response(resp, "search")
        data = resp.json()

        return self._normalize_search(data)

    # ------------------------------------------------------------------
    # Extract API — POST /v1beta/extract
    # ------------------------------------------------------------------

    async def _extract(
        self, params: dict[str, Any], api_key: str
    ) -> dict[str, Any]:
        """Extract content from URLs with auto-batching for >10 URLs.

        Params:
            url (str):                  Single URL to extract.
            urls (list[str]):           Multiple URLs (auto-batched in groups of 10).
            objective (str):            Focus the extraction on this intent.
            search_queries (list[str]): Keywords to emphasize.
            full_content (bool):        Return full page content (not just excerpts).
            max_chars_per_result (int): Max chars per URL excerpt.
        """
        # Collect URLs
        urls = params.get("urls") or []
        if params.get("url"):
            urls = [params["url"]] + list(urls)
        if isinstance(urls, str):
            urls = [urls]
        if not urls:
            raise ProviderError(self.name, "Missing required parameter: url or urls")

        # Build common body fields
        common: dict[str, Any] = {}
        if params.get("objective"):
            common["objective"] = str(params["objective"])[:3000]
        if params.get("search_queries"):
            sq = params["search_queries"]
            if isinstance(sq, str):
                sq = [sq]
            common["search_queries"] = sq

        # Excerpt/content settings
        if params.get("full_content"):
            fc_settings: dict[str, Any] = {"max_chars_per_result": 50000}
            if params.get("max_chars_per_result"):
                fc_settings["max_chars_per_result"] = int(params["max_chars_per_result"])
            common["full_content"] = fc_settings
        else:
            common["excerpts"] = True

        if params.get("max_age_seconds"):
            common["fetch_policy"] = {
                "max_age_seconds": max(600, int(params["max_age_seconds"])),
            }

        # Batch URLs into groups of 10 (Parallel API limit)
        batches = [urls[i:i + _EXTRACT_BATCH_SIZE] for i in range(0, len(urls), _EXTRACT_BATCH_SIZE)]

        if len(batches) == 1:
            # Single batch — no concurrency needed
            return await self._extract_batch(batches[0], common, api_key)

        # Multiple batches — run concurrently with semaphore
        logger.info(
            "Extracting %d URLs in %d batches (max %d concurrent)",
            len(urls), len(batches), _DEFAULT_CONCURRENCY,
        )
        semaphore = asyncio.Semaphore(_DEFAULT_CONCURRENCY)

        async def _run_batch(batch_urls: list[str]) -> dict[str, Any]:
            async with semaphore:
                return await self._extract_batch(batch_urls, common, api_key)

        results = await asyncio.gather(
            *[_run_batch(b) for b in batches],
            return_exceptions=True,
        )

        # Merge results
        return self._merge_extract_results(results, urls)

    async def _extract_batch(
        self,
        urls: list[str],
        common: dict[str, Any],
        api_key: str,
    ) -> dict[str, Any]:
        """Execute a single extract call for up to 10 URLs."""
        body = {"urls": urls, **common}

        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.post(
                f"{PARALLEL_BASE}/v1beta/extract",
                headers=self._headers(api_key),
                json=body,
            )

        self._check_response(resp, "extract")
        data = resp.json()
        return self._normalize_extract(data)

    # ------------------------------------------------------------------
    # Task API — POST /v1/tasks/runs (async enrichment)
    # ------------------------------------------------------------------

    async def _task_run(
        self, params: dict[str, Any], api_key: str
    ) -> dict[str, Any]:
        """Run an async task with structured output schema.

        Params:
            input (str|dict):           The input to process (required).
            processor (str):            Processing tier. Default: "base".
                                        Options: lite, base, core, core2x, pro, ultra, etc.
            output_schema (dict):       JSON Schema for structured output.
            webhook_url (str):          URL for completion callback.
            poll (bool):                Wait for result. Default: True.
            poll_timeout (float):       Max seconds to wait. Default: 300.
        """
        task_input = params.get("input")
        if not task_input:
            raise ProviderError(self.name, "Missing required parameter: input")

        body: dict[str, Any] = {
            "input": task_input,
            "processor": params.get("processor", "base"),
        }

        # Task spec with output schema — wrap in Parallel's expected format
        if params.get("output_schema"):
            raw_schema = params["output_schema"]
            # If user passed a raw JSON Schema, wrap it for Parallel's API
            if isinstance(raw_schema, dict) and "type" not in raw_schema:
                # Assume it's a raw JSON Schema object
                raw_schema = {
                    "type": "json",
                    "json_schema": {
                        "name": params.get("schema_name", "output"),
                        "strict": True,
                        "schema": raw_schema,
                    },
                }
            elif isinstance(raw_schema, dict) and raw_schema.get("type") not in ("json", "text"):
                # It's a JSON Schema with type:object — still needs wrapping
                raw_schema = {
                    "type": "json",
                    "json_schema": {
                        "name": params.get("schema_name", "output"),
                        "strict": True,
                        "schema": raw_schema,
                    },
                }
            body["task_spec"] = {"output_schema": raw_schema}

        if params.get("webhook_url"):
            body["webhook_url"] = params["webhook_url"]

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{PARALLEL_BASE}/v1/tasks/runs",
                headers=self._headers(api_key),
                json=body,
            )

        self._check_response(resp, "task create")
        data = resp.json()
        run_id = data.get("run_id")

        if not run_id:
            raise ProviderError(self.name, "Task creation returned no run_id")

        # Return immediately if not polling
        if not params.get("poll", True):
            return {
                "run_id": run_id,
                "status": data.get("status", "queued"),
                "message": f"Task created. Poll with run_id: {run_id}",
            }

        # Poll for result
        timeout = float(params.get("poll_timeout", _POLL_TIMEOUT))
        return await self._poll_task(run_id, api_key, timeout)

    async def _poll_task(
        self, run_id: str, api_key: str, timeout: float
    ) -> dict[str, Any]:
        """Poll a task run until completion."""
        elapsed = 0.0
        async with httpx.AsyncClient(timeout=30.0) as client:
            while elapsed < timeout:
                resp = await client.get(
                    f"{PARALLEL_BASE}/v1/tasks/runs/{run_id}/result",
                    headers=self._headers(api_key),
                )

                if resp.status_code == 200:
                    data = resp.json()
                    status = data.get("status", "unknown")

                    if status == "completed":
                        return {
                            "run_id": run_id,
                            "status": "completed",
                            "output": data.get("output"),
                            "basis": data.get("basis", []),
                            "usage": data.get("usage"),
                        }

                    if status == "failed":
                        errors = data.get("errors", [])
                        raise ProviderError(
                            self.name,
                            f"Task failed: {errors}",
                        )

                elif resp.status_code == 408:
                    pass  # 408 = still running, keep polling
                elif resp.status_code != 202:
                    # 202 = still processing, anything else is unexpected
                    self._check_response(resp, "task poll")

                await asyncio.sleep(_POLL_INTERVAL)
                elapsed += _POLL_INTERVAL

        raise ProviderError(
            self.name,
            f"Task poll timed out after {timeout}s. run_id: {run_id}",
        )

    # ------------------------------------------------------------------
    # Task Groups API — bulk async processing
    # ------------------------------------------------------------------

    async def _task_group(
        self, params: dict[str, Any], api_key: str
    ) -> dict[str, Any]:
        """Process items in bulk via Task Groups.

        Params:
            items (list[str|dict]):     Items to process (required).
            processor (str):            Processing tier. Default: "base".
            output_schema (dict):       JSON Schema for structured output.
            poll (bool):                Wait for all results. Default: True.
            poll_timeout (float):       Max seconds to wait. Default: 600.
        """
        items = params.get("items", [])
        if not items:
            raise ProviderError(self.name, "Missing required parameter: items")

        processor = params.get("processor", "base")

        # Step 1: Create task group
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{PARALLEL_BASE}/v1beta/tasks/groups",
                headers=self._headers(api_key),
                json={},
            )

        self._check_response(resp, "task group create")
        group_data = resp.json()
        group_id = group_data.get("taskgroup_id")

        if not group_id:
            raise ProviderError(self.name, "Task group creation returned no taskgroup_id")

        logger.info("Created task group %s for %d items", group_id, len(items))

        # Step 2: Add runs in batches of 500
        task_spec = {}
        if params.get("output_schema"):
            task_spec["output_schema"] = params["output_schema"]

        batches = [
            items[i:i + _TASK_GROUP_BATCH_SIZE]
            for i in range(0, len(items), _TASK_GROUP_BATCH_SIZE)
        ]

        async with httpx.AsyncClient(timeout=30.0) as client:
            for batch_idx, batch in enumerate(batches):
                runs = [
                    {
                        "input": item,
                        "processor": processor,
                        **({"task_spec": task_spec} if task_spec else {}),
                    }
                    for item in batch
                ]

                resp = await client.post(
                    f"{PARALLEL_BASE}/v1beta/tasks/groups/{group_id}/runs",
                    headers=self._headers(api_key),
                    json={"runs": runs},
                )
                self._check_response(resp, f"task group add runs (batch {batch_idx + 1})")
                logger.info(
                    "Added batch %d/%d (%d runs) to group %s",
                    batch_idx + 1, len(batches), len(batch), group_id,
                )

        # Step 3: Poll for completion (optional)
        if not params.get("poll", True):
            return {
                "taskgroup_id": group_id,
                "total_items": len(items),
                "status": "processing",
                "message": f"Task group created with {len(items)} items. Poll group_id: {group_id}",
            }

        timeout = float(params.get("poll_timeout", 600.0))
        return await self._poll_task_group(group_id, len(items), api_key, timeout)

    async def _poll_task_group(
        self,
        group_id: str,
        total: int,
        api_key: str,
        timeout: float,
    ) -> dict[str, Any]:
        """Poll a task group until all runs complete."""
        elapsed = 0.0
        async with httpx.AsyncClient(timeout=30.0) as client:
            while elapsed < timeout:
                resp = await client.get(
                    f"{PARALLEL_BASE}/v1beta/tasks/groups/{group_id}",
                    headers=self._headers(api_key),
                )
                self._check_response(resp, "task group status")
                data = resp.json()

                is_active = data.get("is_active", True)
                counts = data.get("task_run_status_counts", {})
                completed = counts.get("completed", 0)
                failed = counts.get("failed", 0)
                done = completed + failed

                logger.info(
                    "Group %s: %d/%d done (%d completed, %d failed)",
                    group_id, done, total, completed, failed,
                )

                if not is_active or done >= total:
                    # Fetch all results via streaming
                    results = await self._fetch_group_results(group_id, api_key)
                    return {
                        "taskgroup_id": group_id,
                        "total": total,
                        "completed": completed,
                        "failed": failed,
                        "results": results,
                    }

                await asyncio.sleep(_POLL_INTERVAL)
                elapsed += _POLL_INTERVAL

        raise ProviderError(
            self.name,
            f"Task group poll timed out after {timeout}s. group_id: {group_id}",
        )

    async def _fetch_group_results(
        self, group_id: str, api_key: str
    ) -> list[dict[str, Any]]:
        """Fetch completed results from a task group."""
        results = []
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(
                f"{PARALLEL_BASE}/v1beta/tasks/groups/{group_id}/runs",
                headers=self._headers(api_key),
                params={"include_output": "true"},
            )
            if resp.status_code == 200:
                # This may be SSE stream or JSON depending on endpoint version
                try:
                    data = resp.json()
                    if isinstance(data, list):
                        results = data
                    elif isinstance(data, dict) and "runs" in data:
                        results = data["runs"]
                except Exception:
                    # SSE stream — parse line by line
                    for line in resp.text.split("\n"):
                        line = line.strip()
                        if line.startswith("data:"):
                            import json
                            try:
                                run_data = json.loads(line[5:].strip())
                                results.append(run_data)
                            except json.JSONDecodeError:
                                pass
        return results


    # ------------------------------------------------------------------
    # Chat API — POST /v1beta/chat/completions (grounded Q&A)
    # ------------------------------------------------------------------

    async def _chat(
        self, params: dict[str, Any], api_key: str
    ) -> dict[str, Any]:
        """Web-grounded chat completions with citations.

        Uses Authorization: Bearer header (not x-api-key).

        Params:
            q / query / message (str):      The question to answer (required).
            model (str):                    Processor: "lite", "base", "core", "pro". Default: "base".
            previous_interaction_id (str):  Continue a multi-turn conversation.
        """
        message = params.get("q") or params.get("query") or params.get("message")
        if not message:
            raise ProviderError(self.name, "Missing required parameter: q or message")

        model = params.get("model") or params.get("processor", "base")

        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": str(message)}],
        }

        if params.get("previous_interaction_id"):
            body["previous_interaction_id"] = params["previous_interaction_id"]

        # Chat API uses Bearer auth, not x-api-key
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.post(
                f"{PARALLEL_BASE}/v1beta/chat/completions",
                headers=headers,
                json=body,
            )

        self._check_response(resp, "chat")
        data = resp.json()

        # Normalize response
        choices = data.get("choices", [])
        content = ""
        basis = []
        if choices:
            content = choices[0].get("message", {}).get("content", "")
            basis = choices[0].get("basis", data.get("basis", []))

        return {
            "response": content,
            "interaction_id": data.get("interaction_id", data.get("id")),
            "model": data.get("model"),
            "citations": [
                {"title": b.get("title", ""), "url": b.get("url", "")}
                for b in (basis if isinstance(basis, list) else [])
            ],
        }

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    def _check_response(self, resp: httpx.Response, context: str) -> None:
        """Check an HTTP response and raise ProviderError on failure."""
        if resp.status_code == 429:
            raise ProviderError(
                self.name,
                f"Parallel rate limit exceeded during {context}. "
                "Max 600 req/min for search/extract, 2000/min for tasks.",
                status_code=429,
            )
        if resp.status_code == 401:
            raise ProviderError(
                self.name,
                "Invalid Parallel API key. Check your PARALLEL_KEY.",
                status_code=401,
            )
        if resp.status_code == 422:
            try:
                detail = resp.json().get("error", {}).get("detail", resp.text[:500])
            except Exception:
                detail = resp.text[:500]
            raise ProviderError(
                self.name,
                f"Parallel validation error in {context}: {detail}",
                status_code=422,
            )
        if resp.status_code >= 400:
            raise ProviderError(
                self.name,
                f"Parallel {context} failed: {resp.status_code} {resp.text[:500]}",
                status_code=resp.status_code,
            )

    # ------------------------------------------------------------------
    # Normalization
    # ------------------------------------------------------------------

    def _normalize_search(self, data: dict[str, Any]) -> dict[str, Any]:
        """Normalize Parallel search response to nrev-lite schema."""
        raw_results = data.get("results", [])
        results = []
        for i, r in enumerate(raw_results):
            result: dict[str, Any] = {
                "position": i + 1,
                "url": r.get("url"),
                "title": r.get("title"),
                "publish_date": r.get("publish_date"),
            }
            # Excerpts are the content snippets
            excerpts = r.get("excerpts", [])
            if excerpts:
                result["snippet"] = "\n".join(excerpts)
                result["excerpts"] = excerpts
            results.append({k: v for k, v in result.items() if v is not None})

        normalized: dict[str, Any] = {
            "search_id": data.get("search_id"),
            "results": results,
            "total": len(results),
        }

        if data.get("warnings"):
            normalized["warnings"] = data["warnings"]
        if data.get("usage"):
            normalized["usage"] = data["usage"]

        return normalized

    def _normalize_extract(self, data: dict[str, Any]) -> dict[str, Any]:
        """Normalize Parallel extract response to nrev-lite schema."""
        raw_results = data.get("results", [])
        pages = []
        for r in raw_results:
            page: dict[str, Any] = {
                "url": r.get("url"),
                "title": r.get("title"),
                "publish_date": r.get("publish_date"),
            }
            excerpts = r.get("excerpts", [])
            if excerpts:
                page["content"] = "\n".join(excerpts)
                page["excerpts"] = excerpts
            full = r.get("full_content")
            if full:
                page["full_content"] = full
                page["word_count"] = len(full.split()) if isinstance(full, str) else 0
            pages.append({k: v for k, v in page.items() if v is not None})

        errors = data.get("errors", [])
        normalized_errors = []
        for e in errors:
            normalized_errors.append({
                "url": e.get("url"),
                "error_type": e.get("error_type"),
                "status_code": e.get("http_status_code"),
                "content": e.get("content"),
            })

        result: dict[str, Any] = {
            "extract_id": data.get("extract_id"),
            "pages": pages,
            "total": len(pages),
        }
        if normalized_errors:
            result["errors"] = normalized_errors
            result["failed"] = len(normalized_errors)
        if data.get("warnings"):
            result["warnings"] = data["warnings"]

        return result

    def _merge_extract_results(
        self,
        batch_results: list[dict[str, Any] | BaseException],
        all_urls: list[str],
    ) -> dict[str, Any]:
        """Merge results from multiple extract batches."""
        pages: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        warnings: list[str] = []

        for r in batch_results:
            if isinstance(r, BaseException):
                errors.append({
                    "error_type": "batch_error",
                    "content": str(r),
                })
                continue
            pages.extend(r.get("pages", []))
            errors.extend(r.get("errors", []))
            warnings.extend(r.get("warnings", []))

        result: dict[str, Any] = {
            "pages": pages,
            "total": len(pages),
            "total_requested": len(all_urls),
        }
        if errors:
            result["errors"] = errors
            result["failed"] = len(errors)
        if warnings:
            result["warnings"] = warnings

        return result


register_provider("parallel_web", ParallelWebProvider)
