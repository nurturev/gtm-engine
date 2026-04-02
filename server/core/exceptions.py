"""Custom exception classes for the nrev-lite platform."""

from __future__ import annotations


class NrvError(Exception):
    """Base exception for all nrev-lite application errors."""

    error_code: str = "UNKNOWN_ERROR"
    user_action: str = "Contact support"

    def __init__(self, message: str = "An unexpected error occurred") -> None:
        self.message = message
        super().__init__(self.message)

    def to_detail(self) -> dict:
        """Return a structured error detail dict for API responses."""
        return {
            "error_code": self.error_code,
            "message": self.message,
            "user_action": self.user_action,
        }


class AuthError(NrvError):
    """Authentication or authorisation failure."""

    error_code: str = "AUTH_ERROR"
    user_action: str = "Re-authenticate: nrev-lite auth login"

    def __init__(self, message: str = "Authentication failed") -> None:
        super().__init__(message)


class ForbiddenError(NrvError):
    """The authenticated user lacks permission for the requested action."""

    error_code: str = "FORBIDDEN"
    user_action: str = "Check your permissions"

    def __init__(self, message: str = "Forbidden") -> None:
        super().__init__(message)


class NotFoundError(NrvError):
    """The requested resource does not exist."""

    error_code: str = "NOT_FOUND"
    user_action: str = "Verify the resource exists"

    def __init__(self, resource: str = "Resource", identifier: str = "") -> None:
        detail = f"{resource} not found"
        if identifier:
            detail = f"{resource} '{identifier}' not found"
        super().__init__(detail)


class InsufficientCredits(NrvError):
    """The tenant does not have enough credits for the requested operation."""

    error_code: str = "INSUFFICIENT_CREDITS"
    user_action: str = "Top up credits: nrev-lite credits topup"

    def __init__(self, needed: float, available: float) -> None:
        self.needed = needed
        self.available = available
        super().__init__(
            f"Insufficient credits: need {needed}, have {available}"
        )


class ProviderError(NrvError):
    """An upstream data provider returned an error or is unavailable."""

    error_code: str = "PROVIDER_ERROR"
    user_action: str = "Check provider status: nrev-lite status"

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

    error_code: str = "RATE_LIMITED"
    user_action: str = "Wait and retry"

    def __init__(self, message: str = "Rate limit exceeded") -> None:
        super().__init__(message)
