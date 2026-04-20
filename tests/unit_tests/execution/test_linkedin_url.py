"""Unit tests for the LinkedIn URL normalizer.

Pure function. Zero mocks. Blueprint §7 "Helpers, Validators, Mappers — every
exposed function gets a test."

Contract (from fresh_linkedin_lld.md §1.2):
    - Accept common LinkedIn profile URL variants and return a single canonical
      form: ``https://www.linkedin.com/in/<slug>``.
    - Strip tracking query strings and trailing slashes.
    - Ensure ``https://`` and ``www.`` prefix.
    - Reject non-linkedin.com hosts, ``/company/``, ``/jobs/view/``, ``/posts/``
      paths, and inputs without a parseable host — by raising
      ``ProviderError(400)``.
    - Allowed slug characters: ``[A-Za-z0-9\\-_%]+``.

The helper currently lives inside ``fresh_linkedin.py`` (LLD §4.2 "acceptable
alternative"). Tests import the module-private symbol directly — per blueprint
§5.4 this is the "acceptable fallback" path when extracting to a public helper
would be disproportionate churn. If the helper is later extracted, only the
import line below needs updating.
"""

from __future__ import annotations

import pytest

from server.core.exceptions import ProviderError
from server.execution.providers.fresh_linkedin import (
    _normalize_linkedin_profile_url as normalize_linkedin_url,
)


CANONICAL = "https://www.linkedin.com/in/janedoe"


# ---------------------------------------------------------------------------
# Happy-path normalisation
# ---------------------------------------------------------------------------


class TestNormalizesKnownVariantsToCanonicalForm:
    """Every variant users paste must normalize to the single canonical shape."""

    @pytest.mark.parametrize(
        "raw",
        [
            "https://www.linkedin.com/in/janedoe",
            "http://www.linkedin.com/in/janedoe",
            "https://linkedin.com/in/janedoe",
            "http://linkedin.com/in/janedoe",
            "www.linkedin.com/in/janedoe",
            "linkedin.com/in/janedoe",
            "https://www.linkedin.com/in/janedoe/",
            "https://www.linkedin.com/in/janedoe?utm_source=share",
            "https://www.linkedin.com/in/janedoe/?utm_source=share&utm_medium=web",
            "  https://www.linkedin.com/in/janedoe  ",
            "HTTPS://WWW.LINKEDIN.COM/in/janedoe",
        ],
    )
    def test_normalizes_to_canonical(self, raw: str) -> None:
        assert normalize_linkedin_url(raw) == CANONICAL


class TestPreservesAllowedSlugCharacters:
    """LinkedIn slugs may include hyphens, underscores, digits, and percent-encoded unicode."""

    @pytest.mark.parametrize(
        "slug",
        [
            "jane-doe",
            "jane_doe",
            "jane-doe-42",
            "jd",
            "jane%2Ddoe",  # percent-encoded hyphen
            "%E4%BD%90%E8%97%A4",  # percent-encoded unicode (sato)
        ],
    )
    def test_preserves_slug(self, slug: str) -> None:
        result = normalize_linkedin_url(f"https://linkedin.com/in/{slug}")
        assert result == f"https://www.linkedin.com/in/{slug}"


# ---------------------------------------------------------------------------
# Rejection cases
# ---------------------------------------------------------------------------


class TestRejectsNonLinkedInHosts:
    @pytest.mark.parametrize(
        "raw",
        [
            "https://twitter.com/in/janedoe",
            "https://example.com/in/janedoe",
            "https://linkedin.io/in/janedoe",
            "https://notlinkedin.com/in/janedoe",
        ],
    )
    def test_rejects(self, raw: str) -> None:
        with pytest.raises(ProviderError):
            normalize_linkedin_url(raw)


class TestRejectsNonProfilePaths:
    """Only ``/in/<slug>`` profile URLs are supported by this endpoint."""

    @pytest.mark.parametrize(
        "raw",
        [
            "https://www.linkedin.com/company/acme",
            "https://linkedin.com/company/acme-inc",
            "https://www.linkedin.com/jobs/view/12345",
            "https://www.linkedin.com/posts/janedoe-abc123",
            "https://www.linkedin.com/pub/janedoe",
            "https://www.linkedin.com/",
            "https://www.linkedin.com/feed/",
        ],
    )
    def test_rejects(self, raw: str) -> None:
        with pytest.raises(ProviderError):
            normalize_linkedin_url(raw)


class TestRejectsMalformedInputs:
    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "not-a-url",
            "https:///in/janedoe",       # no host
            "https://www.linkedin.com/in/",  # missing slug
        ],
    )
    def test_rejects(self, raw: str) -> None:
        with pytest.raises(ProviderError):
            normalize_linkedin_url(raw)


class TestRejectsNonStringInputs:
    """The helper has no explicit type guard — non-strings either trip the
    empty-check (`None`, empty containers) or crash on ``.strip()`` / regex
    evaluation. Either outcome is acceptable: "garbage in → error out"."""

    @pytest.mark.parametrize("raw", [None, 42, [], {}, object()])
    def test_rejects(self, raw: object) -> None:
        with pytest.raises(Exception):
            normalize_linkedin_url(raw)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Idempotence — canonical form is a fixed point of the function
# ---------------------------------------------------------------------------


class TestIsIdempotent:
    """Normalising the canonical form returns it unchanged. Round-tripping
    must not mutate output — this pins down the stability callers rely on."""

    def test_canonical_is_fixed_point(self) -> None:
        assert normalize_linkedin_url(CANONICAL) == CANONICAL

    def test_double_application_equals_single(self) -> None:
        once = normalize_linkedin_url("linkedin.com/in/janedoe/?utm_source=x")
        twice = normalize_linkedin_url(once)
        assert once == twice == CANONICAL
