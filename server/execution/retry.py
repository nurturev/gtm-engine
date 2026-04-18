"""Exponential backoff retry helper for provider calls.

Smart retry logic:
- Retries on transient errors: 429 (rate limit), 500+ (server errors), timeouts
- Does NOT retry on permanent errors: 401 (auth), 403 (forbidden), 422 (validation)
- Exponential backoff with jitter to avoid thundering herd
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from server.core.exceptions import ProviderError

T = TypeVar("T")
logger = logging.getLogger(__name__)

# Status codes that should NOT be retried (permanent client errors)
_NON_RETRYABLE_STATUS_CODES = {400, 401, 403, 404, 422}


@dataclass(frozen=True)
class RetryConfig:
    """Per-provider override for the retry policy applied by `retry_with_backoff`.

    Defaults match the global policy used across all providers today. Providers
    that need a different shape (e.g. a fixed long delay with no jitter for
    upstream-rate-limit recovery) can set a class-level `retry_config` on
    `BaseProvider`, and `execute_single` will honour it.
    """

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    jitter: bool = True


def _is_retryable(exc: Exception) -> bool:
    """Determine if an exception represents a transient failure worth retrying.

    Only retries on:
    - 429 (rate limit) — Apollo will accept the request after a cooldown
    - 500+ (server error) — upstream issue, may resolve itself
    - 504 (timeout) — network/server overload
    - No status_code (connection error, DNS failure, etc.)

    Does NOT retry:
    - 401 (bad API key — won't magically become valid)
    - 403 (permission denied — won't change on retry)
    - 422 (bad params — same request will fail the same way)
    - 400 (bad request — same thing)
    """
    if isinstance(exc, ProviderError):
        code = exc.status_code
        if code is None:
            # No status code = connection-level error, worth retrying
            return True
        if code in _NON_RETRYABLE_STATUS_CODES:
            return False
        # 429, 500, 502, 503, 504 — all retryable
        return code >= 429
    # For other exception types, assume retryable
    return True


async def retry_with_backoff(
    func: Callable[..., Any],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: bool = True,
    retryable_exceptions: tuple[type[Exception], ...] = (ProviderError,),
    **kwargs: Any,
) -> Any:
    """Execute *func* with exponential backoff on transient failures.

    Args:
        func: Async callable to retry.
        max_retries: Maximum number of retry attempts.
        base_delay: Initial delay in seconds.
        max_delay: Maximum delay cap in seconds.
        jitter: Whether to add random jitter to the delay.
        retryable_exceptions: Exception types that should trigger a retry.

    Returns:
        The return value of *func*.

    Raises:
        The last exception if all retries are exhausted, or immediately
        if the error is non-retryable (e.g. 401, 403, 422).
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except retryable_exceptions as exc:
            last_exc = exc

            # Don't retry permanent errors
            if not _is_retryable(exc):
                logger.debug(
                    "Non-retryable error (status=%s), raising immediately",
                    getattr(exc, "status_code", None),
                )
                raise

            if attempt == max_retries:
                logger.warning(
                    "All %d retries exhausted for %s",
                    max_retries, func.__name__ if hasattr(func, "__name__") else str(func),
                )
                break

            delay = min(base_delay * (2 ** attempt), max_delay)
            if jitter:
                delay *= 0.5 + random.random()

            logger.info(
                "Retry %d/%d after %.1fs (error: %s)",
                attempt + 1, max_retries, delay, exc,
            )
            await asyncio.sleep(delay)

    raise last_exc  # type: ignore[misc]
