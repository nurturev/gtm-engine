"""Environment-specific URL constants.

Defaults are production. Override via environment variables for staging/dev.
"""

import os

API_BASE_URL = os.environ.get(
    "NREV_API_URL", "https://nrev-lite-api.public.prod.nurturev.com"
)
PLATFORM_BASE_URL = os.environ.get(
    "NREV_PLATFORM_URL", "https://app.nrev.ai"
)
PLATFORM_PAYMENTS_URL = f"{PLATFORM_BASE_URL}/payments"
PLATFORM_USAGE_URL = f"{PLATFORM_BASE_URL}/event-logs"
