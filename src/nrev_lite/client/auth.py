"""Authentication helpers — credential storage and token management."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from nrev_lite.utils.config import CREDENTIALS_FILE, _migrate_legacy, ensure_config_dir, get_api_base_url


def save_credentials(
    access_token: str,
    refresh_token: str,
    user_info: dict[str, Any],
    expires_at: float | None = None,
) -> None:
    """Persist credentials to ~/.nrev-lite/credentials with 600 permissions."""
    ensure_config_dir()
    data = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user_info": user_info,
        "expires_at": expires_at or (time.time() + 3600),
    }
    CREDENTIALS_FILE.write_text(json.dumps(data, indent=2))
    os.chmod(CREDENTIALS_FILE, 0o600)


def load_credentials() -> dict[str, Any] | None:
    """Load credentials from disk. Returns None if missing or corrupt."""
    _migrate_legacy()
    if not CREDENTIALS_FILE.exists():
        return None
    try:
        return json.loads(CREDENTIALS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def clear_credentials() -> None:
    """Delete the credentials file."""
    if CREDENTIALS_FILE.exists():
        CREDENTIALS_FILE.unlink()


def is_authenticated() -> bool:
    """Return True if valid credentials exist on disk."""
    creds = load_credentials()
    return creds is not None and "access_token" in creds


def get_token() -> str | None:
    """Return the current access token, or None."""
    creds = load_credentials()
    if creds is None:
        return None
    return creds.get("access_token")


def refresh_token_if_needed() -> str | None:
    """Check token expiry and refresh if needed. Returns current access token.

    Returns None if refresh fails or no credentials exist.
    """
    creds = load_credentials()
    if creds is None:
        return None

    access_token = creds.get("access_token")
    expires_at = creds.get("expires_at", 0)

    # If the token is still valid for at least 60 seconds, return it
    if time.time() < expires_at - 60:
        return access_token

    # Attempt refresh
    refresh_tok = creds.get("refresh_token")
    if not refresh_tok:
        return None

    base_url = get_api_base_url()
    try:
        resp = httpx.post(
            f"{base_url}/auth/refresh",
            json={"refresh_token": refresh_tok},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        save_credentials(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", refresh_tok),
            user_info=creds.get("user_info", {}),
            expires_at=data.get("expires_at", time.time() + 3600),
        )
        return data["access_token"]
    except (httpx.HTTPError, KeyError):
        return None
