"""Pydantic v2 request/response schemas for the auth module."""

from __future__ import annotations

from pydantic import BaseModel


class GoogleAuthRequest(BaseModel):
    redirect_uri: str | None = None
    code_challenge: str | None = None
    code_verifier: str | None = None


class GoogleAuthResponse(BaseModel):
    auth_url: str


class CallbackQuery(BaseModel):
    code: str
    state: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class UserInfoResponse(BaseModel):
    id: str
    email: str
    name: str | None
    avatar_url: str | None
    tenant_id: str
    role: str


class ExchangeRequest(BaseModel):
    supabase_jwt: str
    tenant_id: int | str
    email: str | None = None
    channel: str = "consultant"


class ExchangeResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
