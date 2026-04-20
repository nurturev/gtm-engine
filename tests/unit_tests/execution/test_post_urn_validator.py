"""Unit tests for the post-URN validator.

Implemented as a method on ``FreshLinkedInProvider._validate_post_urn`` —
reused across ``fetch_post_details``, ``fetch_post_reactions``,
``fetch_post_comments``. All three ops require **bare activity id** form and
reject every alternative the user might plausibly pass.

Contract (HLD Phase 2.2 §6.4, grooming P2-D6):
- Accepts: numeric-only string, 15–20 digits.
- Rejects: ``urn:li:activity:<id>`` prefix, full post URLs, alpha chars,
  empty / None / non-string types.
- Raises ``ProviderError(400)`` with a message that cites the expected shape.

The validator is a provider method (not a module-level function) because it
carries the provider name into the raised ``ProviderError``. We instantiate
a throwaway ``FreshLinkedInProvider()`` per test — no mocks needed.
"""

from __future__ import annotations

import pytest

from server.core.exceptions import ProviderError
from server.execution.providers.fresh_linkedin import FreshLinkedInProvider


class TestAcceptsBareActivityIds:
    """Any numeric string within the observed 15–20 digit band passes."""

    @pytest.mark.parametrize(
        "urn",
        [
            "7450415215956987904",          # 19 — shape in every sample we captured
            "7451139979067449344",          # 19 — from profile_posts fixture
            "745041521595698790",           # 18
            "74504152159569879043",         # 20 — upper bound
            "745041521595698",              # 15 — lower bound
        ],
    )
    def test_valid_urns_return_the_stripped_urn(self, urn: str) -> None:
        # Returns the validated string so callers can use it verbatim in upstream
        # calls. Contract: no exception + returns the bare id.
        provider = FreshLinkedInProvider()
        assert provider._validate_post_urn(urn) == urn


class TestAcceptsWhitespacePaddedInput:
    """The validator strips outer whitespace — callers shouldn't have to
    pre-trim user input."""

    def test_surrounding_whitespace_stripped(self) -> None:
        provider = FreshLinkedInProvider()
        assert provider._validate_post_urn("  7450415215956987904 ") == "7450415215956987904"


class TestRejectsCommonMistakes:
    """The three shapes users will try first. The 400 must fire so the mistake
    surfaces as a caller error, not a mysterious upstream 400."""

    @pytest.mark.parametrize(
        "urn",
        [
            "urn:li:activity:7450415215956987904",
            "https://www.linkedin.com/feed/update/urn:li:activity:7450415215956987904/",
            "https://www.linkedin.com/posts/janedoe_activity-7450415215956987904",
        ],
    )
    def test_structured_forms_rejected(self, urn: str) -> None:
        provider = FreshLinkedInProvider()

        with pytest.raises(ProviderError) as exc_info:
            provider._validate_post_urn(urn)

        assert exc_info.value.status_code == 400
        assert exc_info.value.provider == "fresh_linkedin"


class TestRejectsMalformedInput:
    @pytest.mark.parametrize(
        "bad",
        [
            "",                    # empty string
            "   ",                 # whitespace only
            "abc",                 # non-numeric
            "1234",                # too short (4 digits)
            "12345678901234",      # 14 — just under lower bound
            "123456789012345678901",  # 21 — just over upper bound
            "7450415215956987904a",   # trailing alpha
            "7450-4152-1595-6987904",  # with separators
        ],
    )
    def test_malformed_rejected(self, bad: str) -> None:
        provider = FreshLinkedInProvider()

        with pytest.raises(ProviderError) as exc_info:
            provider._validate_post_urn(bad)

        assert exc_info.value.status_code == 400


class TestRejectsNoneAndNonString:
    @pytest.mark.parametrize("value", [None, 7450415215956987904, [], {}])
    def test_non_string_rejected(self, value) -> None:
        provider = FreshLinkedInProvider()

        with pytest.raises(ProviderError) as exc_info:
            provider._validate_post_urn(value)

        assert exc_info.value.status_code == 400


class TestErrorMessageGuidesTheCaller:
    """The message is part of the contract — it's what the CLI prints back."""

    def test_message_mentions_bare_activity_id(self) -> None:
        provider = FreshLinkedInProvider()

        with pytest.raises(ProviderError) as exc_info:
            provider._validate_post_urn("urn:li:activity:7450415215956987904")

        msg = str(exc_info.value).lower()
        # Don't pin exact wording — but the caller needs to learn what to pass
        # instead. One of these hints must appear.
        assert any(
            hint in msg
            for hint in ("bare", "activity", "numeric", "digit")
        ), f"error message should guide the caller; got: {exc_info.value}"
