"""Custom exception classes for the nrev-lite platform."""

from __future__ import annotations


class NrvError(Exception):
    """Base exception for all nrev-lite application errors."""

    def __init__(self, message: str = "An unexpected error occurred") -> None:
        self.message = message
        super().__init__(self.message)


class AuthError(NrvError):
    """Authentication or authorisation failure."""

    def __init__(self, message: str = "Authentication failed") -> None:
        super().__init__(message)


class ForbiddenError(NrvError):
    """The authenticated user lacks permission for the requested action."""

    def __init__(self, message: str = "Forbidden") -> None:
        super().__init__(message)


class NotFoundError(NrvError):
    """The requested resource does not exist."""

    def __init__(self, resource: str = "Resource", identifier: str = "") -> None:
        detail = f"{resource} not found"
        if identifier:
            detail = f"{resource} '{identifier}' not found"
        super().__init__(detail)


class InsufficientCredits(NrvError):
    """The tenant does not have enough credits for the requested operation."""

    def __init__(self, needed: float, available: float) -> None:
        self.needed = needed
        self.available = available
        super().__init__(
            f"Insufficient credits: need {needed}, have {available}"
        )


class ProviderError(NrvError):
    """An upstream data provider returned an error or is unavailable."""

    def __init__(
        self,
        provider: str,
        message: str = "Provider error",
        status_code: int | None = None,
    ) -> None:
        self.provider = provider
        self.status_code = status_code
        super().__init__(f"[{provider}] {message}")


class RateLimitError(NrvError):
    """The request was rejected due to rate limiting."""

    def __init__(self, message: str = "Rate limit exceeded") -> None:
        super().__init__(message)
