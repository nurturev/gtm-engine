"""HTTP client for communicating with the nrev-lite cloud server."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import httpx

from nrev_lite.client.auth import get_token, load_credentials, refresh_token_if_needed
from nrev_lite.utils.config import get_api_base_url
from nrev_lite.utils.display import print_error


class NrvApiError(Exception):
    """Raised when the nrev-lite API returns an error response."""

    def __init__(self, status_code: int, message: str, detail: Any = None):
        self.status_code = status_code
        self.message = message
        self.detail = detail
        super().__init__(message)


class NrvClient:
    """Thin HTTP client wrapping all nrev-lite server API calls."""

    def __init__(self, base_url: str | None = None, timeout: float = 30):
        self.base_url = (base_url or get_api_base_url()).rstrip("/") + "/api/v1"
        self.timeout = timeout
        self._client: httpx.Client | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            token = refresh_token_if_needed() or get_token()
            headers: dict[str, str] = {}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            self._client = httpx.Client(
                base_url=self.base_url,
                headers=headers,
                timeout=self.timeout,
            )
        return self._client

    def close(self) -> None:
        if self._client and not self._client.is_closed:
            self._client.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        files: Any = None,
        retry_on_401: bool = True,
    ) -> Any:
        """Send a request, handling 401 refresh automatically."""
        try:
            resp = self.client.request(
                method, path, json=json, params=params, files=files
            )
        except httpx.ConnectError:
            print_error(
                f"Could not connect to nrev-lite server at {self.base_url}. "
                "Is the server running?"
            )
            sys.exit(1)
        except httpx.HTTPError as exc:
            print_error(f"HTTP error: {exc}")
            sys.exit(1)

        if resp.status_code == 401 and retry_on_401:
            new_token = refresh_token_if_needed()
            if new_token:
                # Rebuild client with fresh token
                self.close()
                self._client = None
                return self._request(
                    method,
                    path,
                    json=json,
                    params=params,
                    files=files,
                    retry_on_401=False,
                )
            print_error("Session expired. Please run: nrev-lite auth login")
            sys.exit(1)

        if resp.status_code >= 400:
            self._handle_error(resp)

        # 204 No Content has no body
        if resp.status_code == 204:
            return {}
        return resp.json()

    @staticmethod
    def _handle_error(resp: httpx.Response) -> None:
        """Parse an error response and raise an NrvApiError."""
        try:
            body = resp.json()
            message = body.get("message") or body.get("detail") or body.get("error", "Unknown error")
            detail = body.get("detail")
        except Exception:
            message = resp.text or f"HTTP {resp.status_code}"
            detail = None
        raise NrvApiError(resp.status_code, str(message), detail)

    def _get(self, path: str, **params: Any) -> Any:
        cleaned = {k: v for k, v in params.items() if v is not None}
        return self._request("GET", path, params=cleaned)

    def _post(self, path: str, body: Any = None, **kwargs: Any) -> Any:
        return self._request("POST", path, json=body, **kwargs)

    def _delete(self, path: str) -> Any:
        return self._request("DELETE", path)

    # ------------------------------------------------------------------
    # Public convenience methods (used by newer CLI commands)
    # ------------------------------------------------------------------

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Public GET with optional params dict."""
        cleaned = {k: v for k, v in (params or {}).items() if v is not None}
        return self._request("GET", path, params=cleaned)

    def post(self, path: str, json: Any = None) -> Any:
        """Public POST with JSON body."""
        return self._request("POST", path, json=json)

    def patch(self, path: str, json: Any = None) -> Any:
        """Public PATCH with JSON body."""
        return self._request("PATCH", path, json=json)

    def delete(self, path: str) -> Any:
        """Public DELETE."""
        return self._request("DELETE", path)

    # ------------------------------------------------------------------
    # Enrich
    # ------------------------------------------------------------------

    def execute(
        self,
        operation: str,
        params: dict[str, Any],
        *,
        strategy: str | None = None,
        providers: list[str] | None = None,
        dry_run: bool = False,
    ) -> dict:
        body: dict[str, Any] = {
            "operation": operation,
            "params": params,
        }
        if strategy:
            body["strategy"] = strategy
        if providers:
            # Server expects "provider" (singular string), not "providers" (list)
            body["provider"] = providers[0] if len(providers) == 1 else providers[0]
        if dry_run:
            body["dry_run"] = True
        return self._post("/execute", body)

    def execute_batch(
        self,
        operation: str,
        items: list[dict[str, Any]],
        *,
        strategy: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {
            "operation": operation,
            "items": items,
        }
        if strategy:
            body["strategy"] = strategy
        return self._post("/execute/batch", body)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query(self, sql: str, *, mode: str = "read") -> dict:
        return self._post("/query", {"sql": sql, "mode": mode})

    # ------------------------------------------------------------------
    # Tables
    # ------------------------------------------------------------------

    def list_tables(self) -> dict:
        return self._get("/tables")

    def get_table(self, name: str, *, filters: dict[str, Any] | None = None) -> dict:
        params = {}
        if filters:
            params.update(filters)
        return self._get(f"/tables/{name}", **params)

    def add_column(
        self,
        table: str,
        column: str,
        col_type: str,
        *,
        default: Any = None,
    ) -> dict:
        body: dict[str, Any] = {"column": column, "type": col_type}
        if default is not None:
            body["default"] = default
        return self._post(f"/tables/{table}/columns", body)

    # ------------------------------------------------------------------
    # Keys (BYOK)
    # ------------------------------------------------------------------

    def add_key(self, provider: str, api_key: str) -> dict:
        return self._post("/keys", {"provider": provider, "api_key": api_key})

    def list_keys(self) -> dict:
        return self._get("/keys")

    def remove_key(self, provider: str) -> dict:
        return self._delete(f"/keys/{provider}")

    # ------------------------------------------------------------------
    # Credits
    # ------------------------------------------------------------------

    def get_credits(self) -> dict:
        return self._get("/credits")

    def get_credit_history(self, limit: int = 20) -> dict:
        return self._get("/credits/history", limit=limit)

    def get_usage(self) -> dict:
        return self._get("/credits/usage")

    def get_topup_url(self, package: str = "starter") -> dict:
        return self._post("/credits/topup", {"package": package})

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------

    def deploy_dashboard(
        self,
        name: str,
        bundle_path: str,
        queries: dict[str, str] | None = None,
    ) -> dict:
        with open(bundle_path, "rb") as f:
            files = {"bundle": (Path(bundle_path).name, f, "application/octet-stream")}
            params: dict[str, Any] = {"name": name}
            if queries:
                import json as _json
                params["queries"] = _json.dumps(queries)
            return self._request("POST", "/dashboards", params=params, files=files)

    def list_dashboards(self) -> dict:
        return self._get("/dashboards")

    def remove_dashboard(self, name: str) -> dict:
        return self._delete(f"/dashboards/{name}")

    # ------------------------------------------------------------------
    # Auth (server-side endpoints)
    # ------------------------------------------------------------------

    def start_device_auth(self) -> dict:
        return self._request("POST", "/auth/device", retry_on_401=False)

    def poll_device_auth(self, device_code: str) -> dict:
        return self._request(
            "POST",
            "/auth/device/token",
            json={"device_code": device_code},
            retry_on_401=False,
        )
