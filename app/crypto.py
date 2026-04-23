"""Symmetric encryption for VK access tokens at rest.

Fernet wraps AES-128-CBC + HMAC-SHA256; sufficient for "nobody with DB read
access gets plaintext tokens" threat model. Key lives in env (FERNET_KEY).
Losing the key means every stored token becomes unrecoverable — participants
re-auth. This is acceptable and is why the key is backed up separately from
the DB file.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class TokenDecryptionError(Exception):
    """Raised when ciphertext is malformed or the key no longer matches."""


@lru_cache(maxsize=1)
def _cipher() -> Fernet:
    return Fernet(get_settings().fernet_key.encode())


def encrypt_token(plaintext: str) -> bytes:
    return _cipher().encrypt(plaintext.encode("utf-8"))


def decrypt_token(ciphertext: bytes) -> str:
    try:
        return _cipher().decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        raise TokenDecryptionError("token ciphertext is invalid or key rotated") from exc
