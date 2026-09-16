"""Fernet helpers for credential storage at rest."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


def _fernet() -> Fernet:
    settings = get_settings()
    if settings.ofc_fernet_key:
        key = settings.ofc_fernet_key.encode("utf-8")
        # Accept raw url-safe base64 or derive from passphrase
        try:
            return Fernet(key)
        except (ValueError, TypeError):
            digest = hashlib.sha256(settings.ofc_fernet_key.encode("utf-8")).digest()
            return Fernet(base64.urlsafe_b64encode(digest))
    # Derive from secret key so .env alone is enough for local/air-gap installs
    digest = hashlib.sha256(settings.ofc_secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Could not decrypt secret; check OFC_SECRET_KEY / OFC_FERNET_KEY") from exc
