"""Typed shapes for nested LinkedIn-native objects inside the normalized `Person`.

These are `TypedDict` types (not Pydantic models). At runtime they are plain
`dict`s — the typing is documentation + IDE/mypy help only. No runtime
validation, which matches the best-effort normalizer stance: a single malformed
field is skipped and logged, not raised.

`Person` itself remains a plain `dict[str, Any]` for V1 to preserve the existing
normalizer contract. These types describe the items *inside* `Person`'s list
fields (`experience`, `education`, `certifications`, `recent_posts`).

Populated today only by `fresh_linkedin`. Apollo and RocketReach continue to
leave these list fields absent; asymmetry across providers is an accepted
consequence (see HLD §3).
"""

from __future__ import annotations

from typing import TypedDict


class Experience(TypedDict, total=False):
    """One entry in a person's work-history timeline."""

    company: str
    title: str
    start_date: str  # ISO-8601 "YYYY-MM" or "YYYY-MM-DD" when available
    end_date: str  # absent for the current role
    description: str
    location: str


class Education(TypedDict, total=False):
    """One entry in a person's education history."""

    school: str
    degree: str
    field_of_study: str
    start_year: int
    end_year: int


class Certification(TypedDict, total=False):
    """A professional certification earned by the person."""

    name: str
    issuer: str
    year: int


class Post(TypedDict, total=False):
    """A recent LinkedIn post authored by the person."""

    url: str
    text_snippet: str
    posted_at: str  # ISO-8601 timestamp
