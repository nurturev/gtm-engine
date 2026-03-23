"""Abstract base class for all data providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseProvider(ABC):
    """Base class that every provider must implement.

    Providers handle a specific set of operations (e.g. enrich_person,
    search_companies) by calling their upstream API and returning
    normalised results.
    """

    name: str = ""
    supported_operations: list[str] = []

    @abstractmethod
    async def execute(
        self,
        operation: str,
        params: dict[str, Any],
        api_key: str,
    ) -> dict[str, Any]:
        """Execute an operation and return normalised results.

        Args:
            operation: The operation name (e.g. "enrich_person").
            params: Operation-specific parameters.
            api_key: The decrypted API key to use.

        Returns:
            Normalised result dict conforming to the nrev-lite schema.

        Raises:
            ProviderError: If the upstream API returns an error.
        """
        ...

    @abstractmethod
    async def health_check(self, api_key: str) -> bool:
        """Return True if the provider is reachable and the key is valid."""
        ...
