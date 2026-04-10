"""Platform URL constants, with sensible defaults for CLI users."""

from __future__ import annotations

import os

PLATFORM_PAYMENTS_URL = os.environ.get(
    "NREV_PLATFORM_PAYMENTS_URL", "https://app.nrev.ai/payments"
)
PLATFORM_USAGE_URL = os.environ.get(
    "NREV_PLATFORM_USAGE_URL", "https://app.nrev.ai/event-logs"
)
