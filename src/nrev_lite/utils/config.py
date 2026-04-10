"""Configuration file management for nrev-lite."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import sys

if sys.version_info >= (3, 11):
    import tomllib as tomli
else:
    import tomli
import tomli_w

# Honor NREV_LITE_HOME so dev/prod CLIs can keep separate credentials + config.
# Resolved at import time — load_dotenv() must run before this module is imported.
NREV_LITE_DIR = Path(os.environ["NREV_LITE_HOME"]).expanduser() if os.environ.get("NREV_LITE_HOME") else Path.home() / ".nrev-lite"
_LEGACY_DIR = Path.home() / ".nrv"
CONFIG_FILE = NREV_LITE_DIR / "config.toml"
CREDENTIALS_FILE = NREV_LITE_DIR / "credentials"

DEFAULT_API_BASE_URL = "https://nrev-lite-api.public.prod.nurturev.com"
DEFAULT_PLATFORM_BASE_URL = "https://app.nrev.ai"


def _migrate_legacy() -> None:
    """Copy credentials from ~/.nrv/ to ~/.nrev-lite/ if they exist and haven't been migrated."""
    if CREDENTIALS_FILE.exists():
        return  # Already have new credentials
    legacy_creds = _LEGACY_DIR / "credentials"
    if legacy_creds.exists():
        NREV_LITE_DIR.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy2(legacy_creds, CREDENTIALS_FILE)
        os.chmod(CREDENTIALS_FILE, 0o600)
    # Also migrate config.toml if it exists
    legacy_config = _LEGACY_DIR / "config.toml"
    if legacy_config.exists() and not CONFIG_FILE.exists():
        NREV_LITE_DIR.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy2(legacy_config, CONFIG_FILE)


def ensure_config_dir() -> Path:
    """Create ~/.nrev-lite directory if it does not exist."""
    NREV_LITE_DIR.mkdir(parents=True, exist_ok=True)
    _migrate_legacy()
    return NREV_LITE_DIR


def load_config() -> dict[str, Any]:
    """Load config.toml, returning an empty dict if it does not exist."""
    if not CONFIG_FILE.exists():
        return {}
    with open(CONFIG_FILE, "rb") as f:
        return tomli.load(f)


def save_config(data: dict[str, Any]) -> None:
    """Save config data to config.toml."""
    ensure_config_dir()
    with open(CONFIG_FILE, "wb") as f:
        tomli_w.dump(data, f)


def get_config(key: str) -> Any:
    """Get a config value by dotted key (e.g. 'server.url').

    Returns None if the key does not exist.
    """
    config = load_config()
    parts = key.split(".")
    current: Any = config
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def set_config(key: str, value: Any) -> None:
    """Set a config value by dotted key (e.g. 'server.url')."""
    config = load_config()
    parts = key.split(".")
    current = config
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    # Try to coerce value to appropriate type
    if isinstance(value, str):
        if value.lower() in ("true", "false"):
            value = value.lower() == "true"
        else:
            try:
                value = int(value)
            except ValueError:
                try:
                    value = float(value)
                except ValueError:
                    pass
    current[parts[-1]] = value
    save_config(config)


def get_api_base_url() -> str:
    """Get the server URL.

    Resolution order:
      1. `NREV_API_URL` env var (lets dev/prod CLIs target different servers)
      2. `server.url` in `~/.nrev-lite/config.toml`
      3. Default (production)
    """
    env_url = os.environ.get("NREV_API_URL")
    if env_url:
        return env_url.rstrip("/")
    url = get_config("server.url")
    if url and isinstance(url, str):
        return url.rstrip("/")
    return DEFAULT_API_BASE_URL


def get_platform_base_url() -> str:
    """Get the platform (nrev-ui-2) base URL.

    Resolution order:
      1. `platform.url` in `~/.nrev-lite/config.toml`
      2. `NREV_PLATFORM_URL` env var
      3. Default (`https://app.nrev.ai`)

    Used by the CLI auth flow to redirect the user to the platform's `/login`
    page (which handles Supabase SSO and the gtm-engine token exchange).
    """
    url = get_config("platform.url")
    if url and isinstance(url, str):
        return url.rstrip("/")
    env_url = os.environ.get("NREV_PLATFORM_URL")
    if env_url:
        return env_url.rstrip("/")
    return DEFAULT_PLATFORM_BASE_URL
