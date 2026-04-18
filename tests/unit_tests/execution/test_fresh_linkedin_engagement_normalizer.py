"""Phase 2.3 — Reaction and Comment normalizer tests.

Unit tests for the engagement envelope builders:
- ``_normalize_fresh_linkedin_reactions(raw)``
- ``_normalize_fresh_linkedin_comments(raw)``

Pure function. Zero mocks.

Contracts (HLD Phase 2.3 §3, §5):
- ``Reaction``: ``type``, ``reactor{name, headline, linkedin_url, urn}``.
- ``Comment``: ``text``, ``created_at``, ``commenter{name, headline,
  linkedin_url, urn, image_url}``, ``pinned``, ``reply_count``,
  ``additional_data`` (preserves ``permalink``, ``thread_urn``,
  ``annotation``, raw ``replies[]``).
- **P2-D5 — honesty rule:** reactor/commenter is NOT coerced to a full
  Person. No name-splitting. URL stays URN-style (``linkedin.com/in/ACoAA...``).
- Envelopes use ``cursor`` uniformly for pagination (vendor's
  ``pagination_token`` is renamed to ``cursor`` on the way out).
"""

from __future__ import annotations

from server.execution.normalizer import (
    _normalize_fresh_linkedin_comments,
    _normalize_fresh_linkedin_reactions,
)
from tests.unit_tests.execution.fixtures import (
    post_comments_response,
    post_reactions_response,
)


# ---------------------------------------------------------------------------
# Reactions envelope — 11 LIKE reactions in the fixture
# ---------------------------------------------------------------------------


class TestReactionsEnvelope:
    def test_envelope_has_reactions_list_and_total(self) -> None:
        envelope = _normalize_fresh_linkedin_reactions(post_reactions_response())

        assert isinstance(envelope.get("reactions"), list)
        assert envelope.get("total") == 11
        assert len(envelope["reactions"]) == 11

    def test_enrichment_sources_records_reactions_operation(self) -> None:
        envelope = _normalize_fresh_linkedin_reactions(post_reactions_response())
        assert envelope.get("enrichment_sources") == {"fresh_linkedin": ["reactions"]}

    def test_cursor_key_present_even_when_null(self) -> None:
        """Our sample's first-page call returned all 11 reactions with no
        pagination hint. Envelope must expose ``cursor`` (present-with-null)
        — callers treat null as 'no more pages'."""
        envelope = _normalize_fresh_linkedin_reactions(post_reactions_response())
        assert "cursor" in envelope
        assert envelope["cursor"] is None


class TestReactionItemShape:
    def test_type_and_reactor_present_per_item(self) -> None:
        envelope = _normalize_fresh_linkedin_reactions(post_reactions_response())
        first = envelope["reactions"][0]

        assert first["type"] == "LIKE"
        assert isinstance(first["reactor"], dict)

    def test_reactor_has_expected_primary_fields(self) -> None:
        envelope = _normalize_fresh_linkedin_reactions(post_reactions_response())
        first = envelope["reactions"][0]

        assert first["reactor"]["name"] == "Priyanka Sharma Arora"
        assert first["reactor"]["headline"] == "Director - GTM"
        assert first["reactor"]["urn"] == "ACoAAAPWDUYBCoo67pt82mYC2ZlWUCXXBB4lpSQ"


class TestReactorUrnStyleUrlPreserved:
    """P2-D5: reactor.linkedin_url is URN-form (``/in/ACoAA...``). The
    normalizer must NOT rewrite it to the slug form — passing the URN back
    to Apollo/RocketReach still works (with a known hit-rate drop), and
    rewriting would silently change a caller-visible identifier."""

    def test_urn_style_url_is_preserved_verbatim(self) -> None:
        envelope = _normalize_fresh_linkedin_reactions(post_reactions_response())
        first = envelope["reactions"][0]

        assert first["reactor"]["linkedin_url"] == (
            "https://www.linkedin.com/in/ACoAAAPWDUYBCoo67pt82mYC2ZlWUCXXBB4lpSQ"
        )
        assert "ACoAA" in first["reactor"]["linkedin_url"]


class TestReactorSnippetDoesNotLeakPersonPrimaryFields:
    """P2-D5 honesty rule — reactor is a 4-field snippet, not a full ``Person``.
    No invented ``first_name``/``last_name``/``title`` via string splitting."""

    def test_reactor_has_no_first_last_split(self) -> None:
        envelope = _normalize_fresh_linkedin_reactions(post_reactions_response())
        first = envelope["reactions"][0]

        for forbidden in ("first_name", "last_name", "title"):
            assert forbidden not in first["reactor"], (
                f"reactor must not contain '{forbidden}' — name splitting "
                "would fabricate data (P2-D5)"
            )


# ---------------------------------------------------------------------------
# Comments envelope
# ---------------------------------------------------------------------------


class TestCommentsEnvelope:
    def test_envelope_has_comments_list_and_total(self) -> None:
        envelope = _normalize_fresh_linkedin_comments(post_comments_response())

        assert isinstance(envelope.get("comments"), list)
        assert envelope.get("total") == 1

    def test_cursor_passes_through_from_vendor_pagination_token(self) -> None:
        """Vendor's ``pagination_token`` is renamed to ``cursor`` at our
        surface — uniformly across engagement endpoints."""
        envelope = _normalize_fresh_linkedin_comments(post_comments_response())
        assert envelope.get("cursor") == (
            "1491721209-1776506342006-390222d6fca581a614f1f77734aad0e9"
        )

    def test_enrichment_sources_records_comments_operation(self) -> None:
        envelope = _normalize_fresh_linkedin_comments(post_comments_response())
        assert envelope.get("enrichment_sources") == {"fresh_linkedin": ["comments"]}


class TestCommentItemShape:
    def test_text_and_created_at_promoted(self) -> None:
        envelope = _normalize_fresh_linkedin_comments(post_comments_response())
        first = envelope["comments"][0]

        assert first["text"].startswith("Love the framing")
        # created_at derived from vendor's ISO ``created_datetime`` — NOT the
        # raw epoch-ms ``created_at``.
        assert first["created_at"] == "2026-04-16 11:15:56"

    def test_pinned_bool_passed_through(self) -> None:
        envelope = _normalize_fresh_linkedin_comments(post_comments_response())
        first = envelope["comments"][0]
        assert first.get("pinned") is False

    def test_commenter_snippet_includes_image_url(self) -> None:
        """Comments carry ``image_url`` on the commenter (reactions don't).
        Verify it lives on the commenter sub-dict, not in additional_data."""
        envelope = _normalize_fresh_linkedin_comments(post_comments_response())
        first = envelope["comments"][0]

        assert first["commenter"]["name"] == "Vishen Lakhiani"
        assert first["commenter"]["image_url"] is not None
        assert first["commenter"]["image_url"].startswith("https://media.licdn.com/")

    def test_reply_count_derived_from_replies_list_length(self) -> None:
        envelope = _normalize_fresh_linkedin_comments(post_comments_response())
        first = envelope["comments"][0]
        # Fixture has replies=[] → reply_count must be 0.
        assert first["reply_count"] == 0


class TestCommentAdditionalDataPreservesReplies:
    """The raw ``replies[]`` list is preserved inside ``additional_data`` so
    a caller wanting nested reply threading can access them later
    (Phase 2.3 out-of-scope: nested reply normalization)."""

    def test_permalink_in_additional_data(self) -> None:
        envelope = _normalize_fresh_linkedin_comments(post_comments_response())
        first = envelope["comments"][0]

        extras = first.get("additional_data") or {}
        assert extras.get("permalink", "").startswith("https://www.linkedin.com/")
