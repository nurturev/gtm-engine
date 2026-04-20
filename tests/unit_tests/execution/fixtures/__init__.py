"""Fresh LinkedIn Phase-2 test fixture loaders.

Thin helpers that load the vendor sample-response JSONs shipped under
``docs/sample_responses/``. Every post-family / company test uses these
(no inline copy-paste of large JSON in test files).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_SAMPLE_DIR = Path(__file__).resolve().parents[4] / "docs" / "fresh_linkedin_sample_responses"


def _load(slug: str) -> dict[str, Any]:
    path = _SAMPLE_DIR / f"{slug}.json"
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def company_by_url_response() -> dict[str, Any]:
    """Full Google sample — high-fidelity, 100% confident_score."""
    return _load("company_by_url")["response"]


def company_by_domain_response() -> dict[str, Any]:
    """Google Japan — the fuzzy-domain case (80% confident_score)."""
    return _load("company_by_domain")["response"]


def profile_posts_response() -> dict[str, Any]:
    return _load("profile_posts")["response"]


def company_posts_response() -> dict[str, Any]:
    return _load("company_posts")["response"]


def post_details_response() -> dict[str, Any]:
    return _load("post_details")["response"]


def post_reactions_response() -> dict[str, Any]:
    return _load("post_reactions")["response"]


def post_comments_response() -> dict[str, Any]:
    return _load("post_comments")["response"]


def search_posts_response() -> dict[str, Any]:
    return _load("search_posts")["response"]
