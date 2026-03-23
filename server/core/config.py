"""Application settings loaded from environment variables or .env file."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """nrev-lite API configuration.

    All values are loaded from environment variables or a .env file.
    Required fields must be set before the application starts.
    """

    # --- Database ---
    DATABASE_URL: str = "postgresql+asyncpg://nrev_lite:nrev_lite@localhost:5432/nrev_lite"

    # --- Redis ---
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- JWT ---
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # --- Google OAuth ---
    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/v1/auth/callback"

    # --- Stripe (optional) ---
    STRIPE_SECRET_KEY: str | None = None
    STRIPE_WEBHOOK_SECRET: str | None = None

    # --- AWS ---
    AWS_REGION: str = "us-east-1"

    # --- Platform API Keys (optional, used as fallback when no BYOK key) ---
    # These are the nrev-lite-managed keys for providers. Tenants without BYOK keys
    # use these and pay credits. NEVER exposed to users.
    APOLLO_API_KEY: str | None = None
    ROCKETREACH_API_KEY: str | None = None
    ROCKETREACH_API: str | None = None  # alias — some users set this instead
    RAPIDAPI_KEY: str | None = None
    PREDICTLEADS_API_TOKEN: str | None = None
    PREDICTLEADS_API_KEY: str | None = None
    COMPOSIO_API_KEY: str | None = None
    # Parallel Web Systems (parallel.ai) — web scraping & intelligence
    PARALLEL_KEY: str | None = None
    # RapidAPI Real-Time Web Search (OpenWeb Ninja)
    X_RAPIDAPI_KEY: str | None = None

    # --- App ---
    ENVIRONMENT: str = "development"
    CORS_ALLOWED_ORIGINS: str = ""

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,  # allow case-insensitive env var matching
        "extra": "ignore",  # ignore extra env vars not defined in Settings
    }


settings = Settings()  # type: ignore[call-arg]
