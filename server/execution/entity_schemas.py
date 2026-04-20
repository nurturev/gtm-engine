"""Shared contracts for normalized Person and Company outputs across providers.

Every enrichment provider's normalizer emits a fixed set of primary fields plus
an opaque `additional_data` dict keyed by provider name. Callers read primary
fields for cross-provider consistency and reach into `additional_data` for
vendor-specific extras.

Primary-field lists from unique_entity_fields.csv.
"""

from __future__ import annotations

from typing import Any

PERSON_PRIMARY_FIELDS: tuple[str, ...] = (
    "name",
    "first_name",
    "last_name",
    "title",
    "headline",
    "experiences",
    "linkedin_url",
    "email",
    "phone",
    "location",
    "company_name",
    "company_domain",
)

COMPANY_PRIMARY_FIELDS: tuple[str, ...] = (
    "name",
    "domain",
    "linkedin_url",
    "employee_count",
    "industry",
    "hq_location",
)


def _is_falsy(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def build_normalized_row(
    primary: dict[str, Any],
    additional: dict[str, Any],
    provider: str,
) -> dict[str, Any]:
    """Assemble the canonical primary-field + additional_data shape.

    Strips falsy values from both primary and additional. Emits
    `additional_data` only when non-empty. Always emits `enrichment_sources`
    with the list of primary keys populated for this row.
    """
    row: dict[str, Any] = {}
    populated: list[str] = []
    for key, value in primary.items():
        if _is_falsy(value):
            continue
        row[key] = value
        populated.append(key)

    cleaned_additional = {
        k: v for k, v in additional.items() if not _is_falsy(v)
    } if additional else {}
    if cleaned_additional:
        row["additional_data"] = {provider: cleaned_additional}

    row["enrichment_sources"] = {provider: populated}
    return row
