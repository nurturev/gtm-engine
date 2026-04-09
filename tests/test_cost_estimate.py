"""Unit tests for cost estimation helpers and bulk schema validation.

Pure-function tests — no DB, no HTTP. Verifies that:
- `calculate_cost` produces the right numbers across all branches.
- `build_cost_breakdown` produces strings byte-identical to what the
  single-cost endpoint used to inline.
- The Pydantic `BulkCostEstimateRequest` enforces the 1–50 length window.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from server.execution.schemas import (
    BulkCostEstimateRequest,
    CostEstimateRequest,
)
from server.execution.service import (
    build_cost_breakdown,
    calculate_cost,
)


# ---------------------------------------------------------------------------
# calculate_cost
# ---------------------------------------------------------------------------


class TestCalculateCost:
    def test_single_enrich_person(self):
        assert calculate_cost("enrich_person", {"email": "a@x.com"}) == 1.0

    def test_search_default_per_page(self):
        # No per_page → defaults to 25 → 1 credit
        assert calculate_cost("search_people", {}) == 1.0

    def test_search_per_page_50(self):
        assert calculate_cost("search_people", {"per_page": 50}) == 2.0

    def test_search_per_page_100(self):
        assert calculate_cost("search_people", {"per_page": 100}) == 4.0

    def test_search_uses_limit_alias(self):
        assert calculate_cost("search_people", {"limit": 50}) == 2.0

    def test_bulk_enrich_people(self):
        params = {"details": [{"email": f"u{i}@x.com"} for i in range(4)]}
        assert calculate_cost("bulk_enrich_people", params) == 4.0

    def test_bulk_enrich_companies(self):
        params = {"domains": ["a.com", "b.com", "c.com"]}
        assert calculate_cost("bulk_enrich_companies", params) == 3.0

    def test_bulk_empty_returns_minimum_one(self):
        assert calculate_cost("bulk_enrich_people", {"details": []}) == 1.0

    def test_unknown_operation_falls_back_to_one(self):
        assert calculate_cost("not_a_real_op", {}) == 1.0


# ---------------------------------------------------------------------------
# build_cost_breakdown — must match the strings the single endpoint used
# ---------------------------------------------------------------------------


class TestBuildCostBreakdown:
    def test_single_enrichment_string(self):
        assert build_cost_breakdown("enrich_person", {}, 1.0) == (
            "Single enrichment = 1.0 credit"
        )

    def test_search_string_default_page(self):
        assert build_cost_breakdown("search_people", {"per_page": 50}, 2.0) == (
            "Search: 50 results/page × 1 credit per 25 results = 2.0 credits (page 1)"
        )

    def test_search_string_explicit_page(self):
        result = build_cost_breakdown(
            "search_people", {"per_page": 25, "page": 3}, 1.0
        )
        assert result == (
            "Search: 25 results/page × 1 credit per 25 results = 1.0 credits (page 3)"
        )

    def test_bulk_people_string(self):
        params = {"details": [{"email": "a@x.com"}, {"email": "b@x.com"}]}
        assert build_cost_breakdown("bulk_enrich_people", params, 2.0) == (
            "Bulk: 2 records × 1 credit each = 2.0 credits"
        )

    def test_bulk_companies_string(self):
        params = {"domains": ["a.com", "b.com", "c.com"]}
        assert build_cost_breakdown("bulk_enrich_companies", params, 3.0) == (
            "Bulk: 3 records × 1 credit each = 3.0 credits"
        )

    def test_unknown_operation_uses_single_branch(self):
        assert build_cost_breakdown("not_a_real_op", {}, 1.0) == (
            "Single enrichment = 1.0 credit"
        )


# ---------------------------------------------------------------------------
# BulkCostEstimateRequest validation
# ---------------------------------------------------------------------------


class TestBulkCostEstimateRequest:
    def test_accepts_one_item(self):
        req = BulkCostEstimateRequest(
            operations=[CostEstimateRequest(operation="enrich_person")]
        )
        assert len(req.operations) == 1

    def test_accepts_fifty_items(self):
        ops = [CostEstimateRequest(operation="enrich_person") for _ in range(50)]
        req = BulkCostEstimateRequest(operations=ops)
        assert len(req.operations) == 50

    def test_rejects_empty_list(self):
        with pytest.raises(ValidationError) as excinfo:
            BulkCostEstimateRequest(operations=[])
        assert "at least 1" in str(excinfo.value)

    def test_rejects_fifty_one_items(self):
        ops = [CostEstimateRequest(operation="enrich_person") for _ in range(51)]
        with pytest.raises(ValidationError) as excinfo:
            BulkCostEstimateRequest(operations=ops)
        assert "at most 50" in str(excinfo.value)

    def test_accepts_mixed_operation_types(self):
        req = BulkCostEstimateRequest(
            operations=[
                CostEstimateRequest(
                    operation="search_people", params={"per_page": 50}
                ),
                CostEstimateRequest(
                    operation="bulk_enrich_people",
                    params={"details": [{"email": "a@x.com"}, {"email": "b@x.com"}]},
                ),
                CostEstimateRequest(
                    operation="enrich_person", params={"email": "c@x.com"}
                ),
            ]
        )
        assert [o.operation for o in req.operations] == [
            "search_people",
            "bulk_enrich_people",
            "enrich_person",
        ]
