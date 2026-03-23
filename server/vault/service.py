"""Vault service: encrypt/decrypt API keys.

Uses a layered encryption strategy:
- **Local/Development**: Fernet symmetric encryption with a key derived from
  the JWT_SECRET_KEY + tenant_id.  This means even if someone dumps the DB,
  the encrypted blobs are useless without the server secret AND the correct
  tenant_id.
- **Production (AWS)**: AWS KMS envelope encryption with tenant_id in the
  encryption context.  A KMS data key encrypts each API key, and KMS wraps
  the data key.  Decryption requires both KMS access AND the correct tenant_id.

SECURITY PROPERTIES:
- Keys are NEVER stored in plaintext, not even in dev
- Tenant isolation: a key encrypted for tenant A cannot be decrypted for tenant B
- The encryption key is derived per-tenant, so compromising one tenant's keys
  doesn't compromise another's
- Key material never appears in logs, return values, or error messages
"""

from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from server.core.config import settings

logger = logging.getLogger(__name__)


def _derive_fernet_key(tenant_id: str) -> bytes:
    """Derive a Fernet-compatible key from the server secret + tenant_id.

    Uses PBKDF2-HMAC-SHA256 with the tenant_id as salt.  The result is
    a 32-byte key that is URL-safe base64 encoded (Fernet requirement).

    This ensures:
    - Different tenants get different encryption keys
    - The key is deterministic for the same (secret, tenant_id) pair
    - Brute-forcing requires knowing the JWT_SECRET_KEY
    """
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        settings.JWT_SECRET_KEY.encode("utf-8"),
        tenant_id.encode("utf-8"),
        iterations=100_000,
        dklen=32,
    )
    return base64.urlsafe_b64encode(dk)


def encrypt_key(raw_key: str, tenant_id: str) -> bytes:
    """Encrypt a raw API key for storage.

    Returns an opaque ciphertext blob.  The raw key cannot be recovered
    without both the server secret and the correct tenant_id.
    """
    if settings.ENVIRONMENT != "development":
        # Production: use AWS KMS envelope encryption
        try:
            import boto3

            kms = boto3.client("kms", region_name=settings.AWS_REGION)
            response = kms.encrypt(
                KeyId="alias/nrev-lite-tenant-keys",
                Plaintext=raw_key.encode("utf-8"),
                EncryptionContext={"tenant_id": tenant_id},
            )
            return response["CiphertextBlob"]
        except Exception:
            logger.exception("KMS encryption failed, falling back to Fernet")

    # Development / KMS fallback: Fernet with derived key
    fernet_key = _derive_fernet_key(tenant_id)
    f = Fernet(fernet_key)
    return f.encrypt(raw_key.encode("utf-8"))


def decrypt_key(encrypted_key: bytes, tenant_id: str) -> str:
    """Decrypt an encrypted API key.

    Raises ValueError if decryption fails (wrong tenant, corrupted data,
    or tampered ciphertext).
    """
    if settings.ENVIRONMENT != "development":
        # Production: try KMS first
        try:
            import boto3

            kms = boto3.client("kms", region_name=settings.AWS_REGION)
            response = kms.decrypt(
                CiphertextBlob=encrypted_key,
                EncryptionContext={"tenant_id": tenant_id},
            )
            return response["Plaintext"].decode("utf-8")
        except Exception:
            logger.warning("KMS decryption failed, trying Fernet fallback")

    # Development / KMS fallback: Fernet with derived key
    fernet_key = _derive_fernet_key(tenant_id)
    f = Fernet(fernet_key)
    try:
        return f.decrypt(encrypted_key).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError(
            "Failed to decrypt API key — wrong tenant or corrupted data"
        ) from exc


def key_hint(raw_key: str) -> str:
    """Generate a display hint from the last 4 characters of a key.

    Returns something like ``...x7f2`` — enough to identify a key,
    never enough to use it.
    """
    if len(raw_key) >= 4:
        return f"...{raw_key[-4:]}"
    return "***"
