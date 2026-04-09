"""Authentication router: Google OAuth, JWT issuance, Supabase exchange."""

from __future__ import annotations

import json as _json
import logging
import secrets
from datetime import datetime, timezone
from string import Template
from urllib.parse import urlencode

from jose import JWTError, jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.core.config import settings
from server.core.database import get_db
from server.core.security import hash_token
from server.auth.models import RefreshToken, User
from server.auth.schemas import (
    ExchangeRequest,
    ExchangeResponse,
    GoogleAuthRequest,
    GoogleAuthResponse,
    RefreshRequest,
    TokenResponse,
    UserInfoResponse,
)
from server.auth.dependencies import get_current_user
from server.auth.service import (
    build_access_token,
    find_or_create_user,
    generate_tokens,
    google_exchange_code,
    persist_refresh_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_SCOPES = "openid email profile"

# Cookie settings for console browser sessions
_COOKIE_NAME = "nrev_session"
_COOKIE_MAX_AGE = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60  # seconds
_COOKIE_SECURE = settings.ENVIRONMENT != "development"  # HTTPS only in prod

# Redis TTLs
_PENDING_AUTH_TTL = 600  # 10 minutes


# ---------------------------------------------------------------------------
# Redis helpers for auth state
# ---------------------------------------------------------------------------


def _get_redis():
    """Get the Redis connection from the app module."""
    from server.app import redis_pool

    return redis_pool


async def _set_pending_auth(state: str, data: dict[str, str]) -> None:
    """Store pending OAuth state in Redis with TTL."""
    redis = _get_redis()
    if redis is None:
        raise RuntimeError("Redis not available")
    await redis.set(
        f"auth:pending:{state}",
        _json.dumps(data),
        ex=_PENDING_AUTH_TTL,
    )


async def _pop_pending_auth(state: str) -> dict[str, str]:
    """Retrieve and delete pending OAuth state from Redis."""
    redis = _get_redis()
    if redis is None:
        return {}
    key = f"auth:pending:{state}"
    data = await redis.get(key)
    if data is None:
        return {}
    await redis.delete(key)
    return _json.loads(data)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/google", response_model=GoogleAuthResponse)
async def initiate_google_auth(body: GoogleAuthRequest) -> GoogleAuthResponse:
    """Return the Google OAuth consent URL.

    The CLI sends its localhost callback URL. We store it keyed by state,
    then redirect the user to Google. Google redirects back to our server
    callback, which looks up the CLI redirect and sends the browser there.
    """
    state = secrets.token_urlsafe(32)

    # Store the CLI's localhost redirect and PKCE verifier so the callback
    # can complete the exchange.
    await _set_pending_auth(
        state,
        {
            "cli_redirect": body.redirect_uri or "",
            "code_verifier": body.code_verifier or "",
        },
    )

    # Google always redirects to our server callback (registered in Google Console)
    server_callback = settings.GOOGLE_REDIRECT_URI

    auth_url = (
        f"{GOOGLE_AUTH_URL}"
        f"?client_id={settings.GOOGLE_CLIENT_ID}"
        f"&redirect_uri={server_callback}"
        f"&response_type=code"
        f"&scope={GOOGLE_SCOPES}"
        f"&access_type=offline"
        f"&prompt=consent"
        f"&state={state}"
    )

    # Append PKCE code_challenge if the client provided one
    if body.code_challenge:
        auth_url += f"&code_challenge={body.code_challenge}&code_challenge_method=S256"

    return GoogleAuthResponse(auth_url=auth_url)


# ---------------------------------------------------------------------------
# Console browser login (cookie-based)
# ---------------------------------------------------------------------------

_LOGIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign In — nrev-lite</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
     background:#0a0a0a;color:#e0e0e0;display:flex;align-items:center;
     justify-content:center;min-height:100vh}
.card{background:#141414;border:1px solid #222;border-radius:16px;padding:48px;
      text-align:center;max-width:400px;width:90%}
h1{font-size:28px;margin-bottom:8px;color:#fff}
.subtitle{color:#888;margin-bottom:32px;font-size:14px}
.btn{display:inline-flex;align-items:center;gap:10px;padding:12px 28px;
     background:#fff;color:#000;border:none;border-radius:8px;font-size:15px;
     font-weight:500;cursor:pointer;text-decoration:none;transition:opacity 0.2s}
.btn:hover{opacity:0.85}
.btn svg{width:20px;height:20px}
.error{background:#2d1515;border:1px solid #5c2020;color:#f87171;padding:12px;
       border-radius:8px;margin-bottom:24px;font-size:13px}
.footer{margin-top:24px;font-size:12px;color:#555}
</style>
</head>
<body>
<div class="card">
<h1>nrev-lite</h1>
<p class="subtitle">Sign in to your GTM console</p>
$error_html
<a class="btn" href="$auth_url">
<svg viewBox="0 0 48 48"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>
Sign in with Google
</a>
<p class="footer">Powered by nRev</p>
</div>
</body>
</html>"""


@router.get("/login", response_class=HTMLResponse)
async def console_login_page(error: str | None = None):
    """Render a sign-in page for browser-based console access."""
    state = secrets.token_urlsafe(32)
    await _set_pending_auth(
        state,
        {
            "cli_redirect": "",
            "code_verifier": "",
            "console_login": "1",
        },
    )

    server_callback = settings.GOOGLE_REDIRECT_URI
    auth_url = (
        f"{GOOGLE_AUTH_URL}"
        f"?client_id={settings.GOOGLE_CLIENT_ID}"
        f"&redirect_uri={server_callback}"
        f"&response_type=code"
        f"&scope={GOOGLE_SCOPES}"
        f"&access_type=offline"
        f"&prompt=consent"
        f"&state={state}"
    )

    error_html = ""
    if error:
        error_html = f'<div class="error">{error}</div>'

    return HTMLResponse(
        Template(_LOGIN_PAGE_HTML).safe_substitute(
            auth_url=auth_url,
            error_html=error_html,
        )
    )


@router.get("/logout")
async def console_logout():
    """Clear the session cookie and redirect to login."""
    response = RedirectResponse(url="/api/v1/auth/login", status_code=302)
    response.delete_cookie(_COOKIE_NAME, path="/")
    return response


@router.get("/callback")
async def google_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Handle the Google OAuth callback.

    Google redirects here after user consents. We exchange the code for
    tokens, create/find the user, then redirect the browser to the CLI's
    localhost callback with the nrev-lite tokens as query params.
    """
    # Look up where the CLI is listening (do this early so we can redirect errors)
    pending = await _pop_pending_auth(state) if state else {}
    cli_redirect = pending.get("cli_redirect", "")
    code_verifier = pending.get("code_verifier", "")

    # Handle Google errors (user denied access, etc.)
    if error:
        logger.warning("Google OAuth error: %s", error)
        if cli_redirect:
            return RedirectResponse(url=f"{cli_redirect}?error={error}")
        raise HTTPException(status_code=400, detail=f"Google auth error: {error}")

    if not code:
        if cli_redirect:
            return RedirectResponse(url=f"{cli_redirect}?error=no_authorization_code")
        raise HTTPException(status_code=400, detail="No authorization code received")

    # Exchange the Google auth code for user info (include PKCE verifier)
    try:
        google_user = await google_exchange_code(
            code, code_verifier=code_verifier or None
        )
    except (ValueError, Exception) as exc:
        logger.error("Google token exchange failed: %s", exc)
        error_msg = str(exc)[:200]
        if cli_redirect:
            return RedirectResponse(url=f"{cli_redirect}?error={error_msg}")
        raise HTTPException(status_code=400, detail=f"Google auth failed: {error_msg}")

    user = await find_or_create_user(db, google_user)
    tokens = await generate_tokens(db, user)

    user_info = {
        "email": user.email,
        "name": user.name or "",
        "tenant": user.tenant_id,
    }

    logger.info("Auth success: email=%s tenant=%s", user.email, user.tenant_id)

    if cli_redirect:
        # Redirect browser to CLI's localhost with tokens
        params = urlencode(
            {
                "access_token": tokens["access_token"],
                "refresh_token": tokens["refresh_token"],
                "expires_in": str(settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60),
                "user_info": _json.dumps(user_info),
            }
        )
        return RedirectResponse(url=f"{cli_redirect}?{params}")

    # No CLI redirect → this is a browser-based console login.
    # Always set cookie and redirect to dashboard.
    # Logic: CLI flow always has cli_redirect (handled above). If we reach here,
    # it's a browser login. No need to check _pending_auth state which is lost
    # on server reload anyway.
    response = RedirectResponse(
        url=f"/console/{user.tenant_id}",
        status_code=302,
    )
    response.set_cookie(
        key=_COOKIE_NAME,
        value=tokens["access_token"],
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        secure=_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return response


@router.post("/exchange", response_model=ExchangeResponse)
async def exchange_supabase_token(
    body: ExchangeRequest,
    db: AsyncSession = Depends(get_db),
) -> ExchangeResponse:
    """Exchange a Supabase JWT + tenant_id for a gtm-engine access + refresh pair.

    Used by platform microservices (consultant agent, user management
    orchestrator) that already have a Supabase JWT and know the tenant_id.
    Issues a gtm-engine JWT carrying tenant_id in claims so execution
    endpoints can set RLS context.

    Per spec §13, the response always includes a refresh token for both
    `cli` and `consultant` channels — the consultant simply ignores it.
    The CLI requires it because it does not retain a Supabase JWT after
    login and would otherwise re-authenticate every 24 hours.

    No User or Tenant DB records are created here.
    """
    if not settings.SUPABASE_JWT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Token exchange not configured (SUPABASE_JWT_SECRET not set)",
        )

    # Validate the Supabase JWT
    try:
        supabase_payload = jwt.decode(
            body.supabase_jwt,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid Supabase token: {exc}",
        ) from exc

    supabase_user_id: str | None = supabase_payload.get("sub")
    if not supabase_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Supabase token missing subject claim",
        )

    if not body.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tenant_id is required",
        )

    tenant_id_str = str(body.tenant_id)
    email = body.email or supabase_payload.get("email", "")

    access_token = build_access_token(
        subject_id=supabase_user_id,
        tenant_id=tenant_id_str,
        email=email,
        channel=body.channel,
    )
    refresh_token = await persist_refresh_token(
        db,
        subject_id=supabase_user_id,
        tenant_id=tenant_id_str,
        email=email,
        channel=body.channel,
    )

    logger.info(
        "Token exchange: supabase_sub=%s tenant=%s channel=%s refresh_token_issued=true",
        supabase_user_id,
        tenant_id_str,
        body.channel,
    )

    return ExchangeResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_access_token(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Exchange a valid refresh token for a new access + refresh pair.

    Rotation: the existing row is deleted and a fresh pair is issued, so a
    stolen refresh token is single-use. Per-token claims are read directly
    off the stored row — no Supabase or local users/tenants lookup required.
    """
    token_hash = hash_token(body.refresh_token)
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.expires_at > datetime.now(timezone.utc),
        )
    )
    stored = result.scalar_one_or_none()
    if stored is None:
        logger.warning("Refresh token lookup failed: lookup_failed=true")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    subject_id = stored.subject_id
    tenant_id = stored.tenant_id
    email = stored.email
    channel = stored.channel

    await db.delete(stored)
    await db.commit()

    access_token = build_access_token(
        subject_id=subject_id,
        tenant_id=tenant_id,
        email=email,
        channel=channel,
    )
    new_refresh = await persist_refresh_token(
        db,
        subject_id=subject_id,
        tenant_id=tenant_id,
        email=email,
        channel=channel,
    )

    logger.info(
        "Refresh token rotated: subject_id=%s tenant=%s channel=%s rotated=true",
        subject_id,
        tenant_id,
        channel,
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh,
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.get("/me", response_model=UserInfoResponse)
async def get_current_user_info(
    user: User = Depends(get_current_user),
) -> UserInfoResponse:
    """Return profile information for the authenticated user."""
    return UserInfoResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        avatar_url=user.avatar_url,
        tenant_id=user.tenant_id,
        role=user.role,
    )
