"""Auth helpers for VK ID flow."""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any

import httpx

VK_ID_OAUTH_URL = "https://id.vk.ru/oauth2/auth"
_VK_ID_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


def generate_state() -> str:
    """CSRF token stored in a signed session cookie and echoed back on POST."""
    return secrets.token_urlsafe(24)


def generate_code_verifier() -> str:
    """PKCE code_verifier for server-side code exchange."""
    return secrets.token_urlsafe(48)


def generate_code_challenge(code_verifier: str) -> str:
    """S256 code_challenge expected by VK ID OAuth 2.1."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


class VkIdExchangeError(Exception):
    """VK ID refused to exchange the authorization code."""

    def __init__(self, error: str, error_description: str = "") -> None:
        self.error = error
        self.error_description = error_description
        super().__init__(f"{error}: {error_description}".strip(": "))


async def exchange_code_for_access_token(
    *,
    client_id: int,
    redirect_uri: str,
    code: str,
    device_id: str,
    code_verifier: str,
    state: str,
) -> str:
    """Exchange VK ID authorization code for an access token on the server.

    We intentionally do this on the backend. If the browser exchanges the code,
    the resulting token becomes bound to the participant's IP and VK API calls
    from our server fail with "access_token was given to another ip address".
    """
    query = {
        "grant_type": "authorization_code",
        "client_id": str(client_id),
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
        "state": state,
        "device_id": device_id,
    }

    try:
        async with httpx.AsyncClient(timeout=_VK_ID_TIMEOUT) as client:
            response = await client.post(
                VK_ID_OAUTH_URL,
                params=query,
                data={"code": code},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:  # pragma: no cover - thin transport wrapper
        raise VkIdExchangeError("network_error", str(exc)) from exc

    data: dict[str, Any] = response.json()
    if "error" in data:
        raise VkIdExchangeError(
            str(data.get("error", "exchange_failed")),
            str(data.get("error_description", "")),
        )

    access_token = data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise VkIdExchangeError("exchange_failed", "VK ID did not return access_token")
    return access_token
