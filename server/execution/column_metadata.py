"""Column metadata computation for JSONB rows.

Pure-Python column profiler — no heavy dependencies. Single pass over rows to compute
per-column statistics: type inference, null%, unique count, min/max, sample values.
"""

from __future__ import annotations

import re
from typing import Any

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _infer_type(values: list[Any]) -> str:
    """Infer the column type from non-null values."""
    if not values:
        return "unknown"

    nums = 0
    urls = 0
    emails = 0
    bools = 0

    for v in values:
        s = str(v)
        if isinstance(v, bool):
            bools += 1
        elif isinstance(v, (int, float)):
            nums += 1
        elif isinstance(v, str):
            if _URL_RE.match(s):
                urls += 1
            elif _EMAIL_RE.match(s):
                emails += 1
            else:
                try:
                    float(s)
                    nums += 1
                except (ValueError, TypeError):
                    pass

    total = len(values)
    if bools > total * 0.5:
        return "boolean"
    if urls > total * 0.5:
        return "url"
    if emails > total * 0.5:
        return "email"
    if nums > total * 0.5:
        return "number"
    return "string"


def compute_column_metadata(rows: list[dict]) -> dict[str, dict]:
    """Compute per-column metadata from a list of JSONB row dicts.

    Returns:
        {
            "column_name": {
                "type": "string|number|url|email|boolean",
                "null_pct": 35.0,
                "unique_count": 42,
                "min": None,          # only for numbers
                "max": None,          # only for numbers
                "sample_values": ["foo", "bar", "baz"],
                "total_rows": 100,
            },
            ...
        }
    """
    if not rows:
        return {}

    # Collect all column names across all rows
    all_cols: set[str] = set()
    for r in rows:
        all_cols.update(r.keys())

    # Skip internal columns
    skip = {"id", "_created_at", "_workflow_id", "_updated_at", "dedup_hash"}
    cols = sorted(c for c in all_cols if c not in skip)

    total = len(rows)
    result: dict[str, dict] = {}

    for col in cols:
        values = [r.get(col) for r in rows]
        non_null = [v for v in values if v is not None and v != "" and v != "null"]
        null_count = total - len(non_null)
        null_pct = round(null_count / total * 100, 1) if total > 0 else 0.0

        # Unique
        unique_set = set()
        for v in non_null:
            unique_set.add(str(v))
        unique_count = len(unique_set)

        # Type inference
        col_type = _infer_type(non_null)

        # Min/max for numeric columns
        col_min = None
        col_max = None
        if col_type == "number":
            numeric_vals = []
            for v in non_null:
                try:
                    numeric_vals.append(float(v))
                except (ValueError, TypeError):
                    pass
            if numeric_vals:
                col_min = min(numeric_vals)
                col_max = max(numeric_vals)
                # Clean up integers
                if col_min == int(col_min):
                    col_min = int(col_min)
                if col_max == int(col_max):
                    col_max = int(col_max)

        # Sample values (first 3 unique non-null)
        samples = []
        seen = set()
        for v in non_null:
            s = str(v)
            if s not in seen and len(samples) < 3:
                samples.append(s[:100])  # truncate long values
                seen.add(s)

        result[col] = {
            "type": col_type,
            "null_pct": null_pct,
            "unique_count": unique_count,
            "min": col_min,
            "max": col_max,
            "sample_values": samples,
            "total_rows": total,
        }

    return result
