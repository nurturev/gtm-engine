"""Entry point for `python -m nrev_lite.mcp.server` and `python -m nrev_lite.mcp`.

load_dotenv() MUST run before importing server, because config.py resolves
NREV_LITE_DIR and URL constants at import time from environment variables.
"""

from dotenv import load_dotenv

load_dotenv()

from nrev_lite.mcp.server import main  # noqa: E402

main()
