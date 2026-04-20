"""Unit tests for the LinkedIn *company* URL normaliser (pure function).

Sibling to the existing profile URL normaliser (tested in
``test_linkedin_url.py``). Used by:
- ``enrich_company`` — gate + canonical URL for ``/get-company-by-linkedinurl``.
- ``fetch_company_posts`` — same gate; any ``/in/`` URL rejected with a precise
  400 so the caller knows they picked the wrong op.

Contract (HLD Phase 2.1 §3.2):
- Accepts: any variant of ``linkedin.com/company/<slug>`` (with/without
  ``https://``, ``www.``, trailing slash, query params).
- Returns: canonical ``https://www.linkedin.com/company/<slug>``.
- Rejects: profile URLs (``/in/``), jobs, posts, school, non-LinkedIn hosts.

Pure function. Zero mocks.
"""

from __future__ import annotations

import pytest

from server.core.exceptions import ProviderError

try:
    from server.execution.providers.fresh_linkedin import _normalize_linkedin_company_url
except ImportError:
    _normalize_linkedin_company_url = None

pytestmark = pytest.mark.skipif(
    _normalize_linkedin_company_url is None,
    reason="Phase 2.1 not yet implemented — _normalize_linkedin_company_url missing",
)


CANONICAL = "https://www.linkedin.com/company/google"


class TestCanonicalisation:
    @pytest.mark.parametrize(
        "raw",
        [
            "https://www.linkedin.com/company/google",
            "https://www.linkedin.com/company/google/",
            "https://linkedin.com/company/google",
            "http://www.linkedin.com/company/google/",
            "www.linkedin.com/company/google",
            "linkedin.com/company/google",
            "https://www.linkedin.com/company/google/?utm_source=x",
        ],
    )
    def test_variants_canonicalise(self, raw: str) -> None:
        assert _normalize_linkedin_company_url(raw) == CANONICAL


class TestPreservesSlug:
    """Slugs are case-sensitive identifiers — don't lowercase them even though
    we canonicalise the host. Observed behaviour in LinkedIn ecosystem."""

    @pytest.mark.parametrize(
        "slug",
        ["google", "microsoft", "Y-Combinator", "some-company-with-dashes", "a_b_c"],
    )
    def test_slug_preserved_across_cases(self, slug: str) -> None:
        result = _normalize_linkedin_company_url(f"https://linkedin.com/company/{slug}")
        assert result == f"https://www.linkedin.com/company/{slug}"


class TestRejectsProfileUrls:
    """Clear message: company op, profile URL passed — this is the single most
    likely caller mistake, the 400 must tell them what to do."""

    @pytest.mark.parametrize(
        "profile_url",
        [
            "https://www.linkedin.com/in/janedoe",
            "https://linkedin.com/in/janedoe/",
            "https://www.linkedin.com/in/mohnishkewlani",
        ],
    )
    def test_profile_url_raises_400(self, profile_url: str) -> None:
        with pytest.raises(ProviderError) as exc_info:
            _normalize_linkedin_company_url(profile_url)

        assert exc_info.value.status_code == 400
        msg = str(exc_info.value).lower()
        assert "profile" in msg or "/in/" in msg


class TestRejectsOtherLinkedInPaths:
    @pytest.mark.parametrize(
        "bad",
        [
            "https://www.linkedin.com/jobs/view/12345",
            "https://www.linkedin.com/posts/someone-abc",
            "https://www.linkedin.com/school/stanford/",
            "https://www.linkedin.com/feed/update/urn:li:activity:123",
            "https://www.linkedin.com/",
            "https://www.linkedin.com/company/",          # empty slug
        ],
    )
    def test_non_company_path_raises_400(self, bad: str) -> None:
        with pytest.raises(ProviderError):
            _normalize_linkedin_company_url(bad)


class TestRejectsNonLinkedInHosts:
    @pytest.mark.parametrize(
        "bad",
        [
            "https://twitter.com/company/google",
            "https://google.com/company/google",
            "not-a-url",
            "",
            "   ",
        ],
    )
    def test_non_linkedin_raises_400(self, bad: str) -> None:
        with pytest.raises(ProviderError):
            _normalize_linkedin_company_url(bad)
