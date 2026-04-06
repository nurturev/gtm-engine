"""Platform URL constants, sourced from environment variables."""

from __future__ import annotations

import os

PLATFORM_PAYMENTS_URL = os.environ["NREV_PLATFORM_PAYMENTS_URL"]
PLATFORM_USAGE_URL = os.environ["NREV_PLATFORM_USAGE_URL"]
