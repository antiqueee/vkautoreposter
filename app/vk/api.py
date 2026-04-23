"""Thin VK API client.

Deliberately not using a VK SDK: they lag behind API versions, and our surface
is tiny (users.get, wall.repost, wall.get). One ``call()`` helper + typed
methods keeps the code honest about what we send and get.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings

_log = logging.getLogger("app.vk.api")

VK_API_BASE = "https://api.vk.com/method"
_DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class VkNetworkError(Exception):
    """Transport-level failure (DNS, timeout, connection reset, 5xx). Retryable."""


@dataclass
class VkApiError(Exception):
    """VK returned an error envelope. See error_code for the matrix handling."""

    method: str
    error_code: int
    error_msg: str

    def __str__(self) -> str:  # pragma: no cover
        return f"VK {self.method} failed: code={self.error_code} msg={self.error_msg!r}"


async def call(method: str, params: dict[str, Any], *, access_token: str) -> Any:
    """POST to api.vk.com/method/{method}. Returns the value of ``response``.

    access_token is sent in the POST body, not the query, so it is not written
    to any proxy access log by default. Never log the raw token.
    """
    settings = get_settings()
    payload: dict[str, Any] = {
        **params,
        "v": settings.vk_api_version,
        "access_token": access_token,
    }
    _log.debug("vk call %s params_keys=%s", method, sorted(params.keys()))

    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            resp = await client.post(f"{VK_API_BASE}/{method}", data=payload)
    except httpx.HTTPError as exc:
        raise VkNetworkError(f"{method}: {exc}") from exc

    if resp.status_code >= 500:
        raise VkNetworkError(f"{method}: HTTP {resp.status_code}")
    resp.raise_for_status()

    data = resp.json()
    if "error" in data:
        err = data["error"]
        raise VkApiError(
            method=method,
            error_code=int(err.get("error_code", -1)),
            error_msg=str(err.get("error_msg", "")),
        )
    return data.get("response")


async def users_get(
    access_token: str,
    user_ids: list[int] | None = None,
    fields: str = "screen_name",
) -> list[dict[str, Any]]:
    """Return user profile(s). Empty user_ids → the token's owner."""
    params: dict[str, Any] = {"fields": fields}
    if user_ids:
        params["user_ids"] = ",".join(str(uid) for uid in user_ids)
    resp = await call("users.get", params, access_token=access_token)
    return resp if isinstance(resp, list) else []


# Error codes referenced across the app. Keep in sync with ARCHITECTURE.md §5.
class VkErrorCode:
    AUTH_FAILED = 5
    TOO_MANY_REQUESTS = 6
    FLOOD_CONTROL = 9
    CAPTCHA_REQUIRED = 14
    ACCESS_DENIED = 15
    VALIDATION_REQUIRED = 17
    USER_DELETED_OR_BANNED = 18
    RATE_LIMIT = 29
    INVALID_PARAM = 100
    WALL_ACCESS_DENIED = 214
