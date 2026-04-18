"""Unit tests for the RocketReach entry in ``VENDOR_CATALOG`` after the
Universal migration (LLD §3.8 T8 + requirements §5.4).

Universal migration locks the following catalog contract for
``rocketreach``:

    - operations include all four ops:
        ``enrich_person``, ``search_people``,
        ``enrich_company``, ``search_companies``
    - each op is priced at **3 credits**
    - ``enrich_company`` and ``search_companies`` were previously absent
      from the catalog (though the provider code supported them) — this
      migration adds them

Pure data tests, zero mocks.
"""

from __future__ import annotations

import pytest

from server.core.vendor_catalog import VENDOR_CATALOG


UNIVERSAL_OPERATIONS = [
    "enrich_person",
    "search_people",
    "enrich_company",
    "search_companies",
]


class TestRocketReachCatalogOperations:
    def test_catalog_entry_exists(self) -> None:
        assert "rocketreach" in VENDOR_CATALOG

    @pytest.mark.parametrize("operation", UNIVERSAL_OPERATIONS)
    def test_each_universal_operation_is_listed(self, operation: str) -> None:
        ops = VENDOR_CATALOG["rocketreach"]["operations"]
        assert operation in ops, (
            f"'{operation}' must be in rocketreach.operations — the "
            "Universal migration exposes it to callers"
        )


class TestRocketReachCatalogPricing:
    """Requirements §5.4: every RocketReach op is priced at 3 credits
    under Universal. The catalog is the fallback when the DB-backed
    ``operation_costs`` cache isn't loaded — it must be in sync."""

    @pytest.mark.parametrize("operation", UNIVERSAL_OPERATIONS)
    def test_each_operation_costs_3_credits(self, operation: str) -> None:
        costs = VENDOR_CATALOG["rocketreach"]["credit_costs"]
        assert costs.get(operation) == 3, (
            f"'{operation}' must cost 3 credits — the Universal pricing "
            "agreed in requirements §5.4"
        )

    def test_no_stale_2_credit_prices_remain(self) -> None:
        """Regression guard against a partial migration that leaves the old
        v2 price on one or two ops. A sneaky 2-credit row would silently
        under-bill tenants."""
        costs = VENDOR_CATALOG["rocketreach"]["credit_costs"]
        for operation in UNIVERSAL_OPERATIONS:
            assert costs.get(operation) != 2, (
                f"'{operation}' still priced at the pre-migration value of 2 "
                "credits — expected 3"
            )


class TestRocketReachCatalogCapabilities:
    def test_platform_key_supported(self) -> None:
        """nRev's platform-managed key is required for Universal calls to
        work at all (requirements §8). The catalog must advertise it."""
        assert VENDOR_CATALOG["rocketreach"]["platform_key"] is True

    def test_byok_remains_advertised(self) -> None:
        """BYOK is being sunset (requirements §2) but the catalog flag
        stays advertised until that sunset ships — assert the status quo
        so this test will break intentionally at sunset time."""
        assert VENDOR_CATALOG["rocketreach"]["byok"] is True
