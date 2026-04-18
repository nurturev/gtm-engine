"""Unit tests for the domain normaliser (pure function).

Used by ``enrich_company`` with ``provider="fresh_linkedin"`` to normalise
``domain`` input before passing it to the upstream ``/get-company-by-domain``
endpoint.

Contract (HLD Phase 2.1 §3.3, grooming Q5):
- Strips protocol, path, ``www.`` prefix, trailing slash.
- Lowercases the result.
- Rejects input without a dot with ``ProviderError(400)``.

Pure function. Zero mocks.
"""

from __future__ import annotations

import pytest

from server.core.exceptions import ProviderError
from server.execution.providers.fresh_linkedin import _normalize_domain


class TestStripsToBareDomain:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("google.com", "google.com"),                       # already bare
            ("https://google.com", "google.com"),               # strip https://
            ("http://google.com", "google.com"),                # strip http://
            ("https://www.google.com", "google.com"),           # strip www.
            ("https://www.google.com/", "google.com"),          # strip trailing slash
            ("https://google.com/about", "google.com"),         # strip path
            ("https://www.google.com/about/us", "google.com"),  # multi-segment path
            ("GOOGLE.COM", "google.com"),                       # lowercase
            ("http://Google.COM/", "google.com"),               # mixed case + trailing
            ("  google.com  ", "google.com"),                   # outer whitespace
            ("api.google.com", "api.google.com"),               # subdomain preserved
            ("www.example.co.uk", "example.co.uk"),             # multi-dot TLD
        ],
    )
    def test_various_inputs(self, raw: str, expected: str) -> None:
        assert _normalize_domain(raw) == expected


class TestRejectsInvalidDomains:
    """Only reject what's unambiguously not-a-domain. Be lenient otherwise —
    the vendor does its own validation for edge cases we'd rather not second-guess."""

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "   ",
            "not a domain",        # whitespace in the middle
            "localhost",           # no TLD
            "https://",            # empty after strip
        ],
    )
    def test_malformed_raises_400(self, bad: str) -> None:
        with pytest.raises(ProviderError) as exc_info:
            _normalize_domain(bad)

        assert exc_info.value.status_code == 400
        assert exc_info.value.provider == "fresh_linkedin"
