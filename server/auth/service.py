"""Authentication business logic: Google OAuth exchange, user resolution, JWT."""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.core.config import settings
from server.auth.models import RefreshToken, Tenant, User

logger = logging.getLogger(__name__)

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

# Common personal/free email domains — users from these get individual tenants
PERSONAL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "yahoo.co.uk",
    "hotmail.com", "outlook.com", "live.com", "msn.com", "aol.com",
    "icloud.com", "me.com", "mac.com", "mail.com", "protonmail.com",
    "proton.me", "zoho.com", "yandex.com", "gmx.com", "gmx.net",
    "fastmail.com", "tutanota.com", "hey.com", "pm.me",
    "rediffmail.com", "inbox.com", "mail.ru", "qq.com", "163.com",
})


async def google_exchange_code(
    code: str,
    *,
    code_verifier: str | None = None,
) -> dict[str, Any]:
    """Exchange a Google authorization code for user profile information.

    Performs two HTTP calls:
    1. Exchange the code for an access token at Google's token endpoint.
    2. Fetch the user's profile from the userinfo endpoint.

    When *code_verifier* is provided (PKCE flow), it is included in the
    token-exchange request so Google can verify it against the original
    ``code_challenge``.

    Returns a dict with keys: ``id``, ``email``, ``name``, ``picture``.
    """
    async with httpx.AsyncClient() as client:
        # Step 1: exchange code for tokens
        token_data: dict[str, str] = {
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        }
        if code_verifier:
            token_data["code_verifier"] = code_verifier

        token_response = await client.post(
            GOOGLE_TOKEN_URL,
            data=token_data,
        )
        if token_response.status_code != 200:
            raise ValueError(
                f"Google token exchange failed: {token_response.status_code} "
                f"{token_response.text}"
            )
        tokens = token_response.json()
        access_token = tokens["access_token"]

        # Step 2: fetch user profile
        userinfo_response = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if userinfo_response.status_code != 200:
            raise ValueError(
                f"Google userinfo failed: {userinfo_response.status_code} "
                f"{userinfo_response.text}"
            )
        return userinfo_response.json()


def _is_personal_domain(domain: str) -> bool:
    """Return True if the domain is a known personal/free email provider."""
    return domain.lower() in PERSONAL_DOMAINS


async def find_or_create_user(
    db: AsyncSession,
    google_user: dict[str, Any],
) -> User:
    """Find an existing user by Google ID or email, or create a new one.

    Domain-based tenant logic:
    - **Business domains** (e.g. @nurturev.com): look for an existing tenant
      with that domain.  If found, the new user joins it as a member.  If not,
      create a new tenant.
    - **Personal domains** (gmail, hotmail, etc.): always create a separate
      tenant per user.

    Free credit allocation now happens entirely on the platform side (the user
    management service is the source of truth). gtm-engine no longer grants
    signup credits.
    """
    google_id = google_user["id"]
    email = google_user["email"]
    name = google_user.get("name")
    picture = google_user.get("picture")

    # ----- Existing user by google_id -----
    result = await db.execute(select(User).where(User.google_id == google_id))
    user = result.scalar_one_or_none()

    if user is not None:
        user.last_login_at = datetime.now(timezone.utc)
        if name and not user.name:
            user.name = name
        if picture:
            user.avatar_url = picture
        await db.commit()
        return user

    # ----- Existing user by email (invite flow) -----
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is not None:
        user.google_id = google_id
        user.last_login_at = datetime.now(timezone.utc)
        if name and not user.name:
            user.name = name
        if picture:
            user.avatar_url = picture
        await db.commit()
        return user

    # ----- Brand-new user — decide tenant -----
    domain = email.split("@")[1].lower() if "@" in email else None
    is_personal = domain is None or _is_personal_domain(domain)

    existing_tenant: Tenant | None = None

    if not is_personal and domain:
        # Business domain — try to find an existing tenant for this domain
        tenant_result = await db.execute(
            select(Tenant).where(Tenant.domain == domain)
        )
        existing_tenant = tenant_result.scalar_one_or_none()

    if existing_tenant is not None:
        # Join the existing business-domain tenant as a member
        tenant = existing_tenant
        role = "member"
        logger.info(
            "User %s joining existing tenant %s (domain: %s)",
            email, tenant.id, domain,
        )
    else:
        # Create a new tenant
        slug = domain.split(".")[0] if domain else "user"
        tenant_id = f"{slug}-{secrets.token_hex(4)}"
        tenant = Tenant(
            id=tenant_id,
            name=name or email,
            domain=domain,
        )
        db.add(tenant)
        role = "owner"

    user_id = f"user_{secrets.token_hex(8)}"
    user = User(
        id=user_id,
        tenant_id=tenant.id,
        email=email,
        name=name,
        google_id=google_id,
        avatar_url=picture,
        role=role,
        last_login_at=datetime.now(timezone.utc),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return user


def build_access_token(
    *,
    subject_id: str,
    tenant_id: str,
    email: str | None,
    channel: str,
    role: str | None = None,
) -> str:
    """Build a signed gtm-engine access JWT.

    Pure function — no DB access. Used by both the legacy Google OAuth path
    and the Supabase exchange path. The `channel` claim distinguishes CLI vs
    consultant callers; `role` is optional and only set by the legacy path.
    """
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload: dict[str, Any] = {
        "sub": subject_id,
        "tenant_id": tenant_id,
        "email": email or "",
        "channel": channel,
        "exp": expire,
        "type": "access",
    }
    if role is not None:
        payload["role"] = role
    return jwt.encode(
        payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )


async def persist_refresh_token(
    db: AsyncSession,
    *,
    subject_id: str,
    tenant_id: str,
    email: str | None,
    channel: str,
) -> str:
    """Generate an opaque refresh token, persist its hash, return the raw value.

    The per-token claims (`tenant_id`, `email`, `channel`) are stored on the
    row so that the refresh endpoint can re-mint an access token on rotation
    without consulting Supabase or the local users/tenants tables.
    """
    raw_refresh = secrets.token_urlsafe(48)
    refresh_hash = hashlib.sha256(raw_refresh.encode()).hexdigest()
    refresh_expires = datetime.now(timezone.utc) + timedelta(
        days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS
    )

    refresh_record = RefreshToken(
        subject_id=subject_id,
        tenant_id=tenant_id,
        email=email,
        channel=channel,
        token_hash=refresh_hash,
        expires_at=refresh_expires,
    )
    db.add(refresh_record)
    await db.commit()
    return raw_refresh


async def generate_tokens(db: AsyncSession, user: User) -> dict[str, str]:
    """Create an access + refresh token pair for the given local user.

    Used by the legacy Google OAuth callback. New callers (Supabase exchange)
    should use `build_access_token` and `persist_refresh_token` directly.
    """
    access_token = build_access_token(
        subject_id=user.id,
        tenant_id=user.tenant_id,
        email=user.email,
        channel="console",
        role=user.role,
    )
    raw_refresh = await persist_refresh_token(
        db,
        subject_id=user.id,
        tenant_id=user.tenant_id,
        email=user.email,
        channel="console",
    )
    return {
        "access_token": access_token,
        "refresh_token": raw_refresh,
    }
