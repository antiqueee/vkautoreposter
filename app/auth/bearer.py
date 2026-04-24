"""Bearer-token auth for the VKMA frontend.

VKMA runs inside a VK iframe (top-level page vk.com, our iframe
app.subscribe-to-reposter.ru). In that third-party context browsers
increasingly block cookies, so Starlette's SessionMiddleware is unreliable
for authenticating API calls from the mini app. Instead, after the user
finishes the Kate OAuth flow we mint a signed bearer token, the frontend
puts it in localStorage, and every API call sends it via
``Authorization: Bearer <token>``.

The token is signed with ``SESSION_SECRET_KEY`` via itsdangerous. It
encodes only ``uid`` (internal users.id) and ``vku`` (vk_user_id, used for
log context). No expiry — it's valid until the user hits /api/vkma/revoke,
which is a server-side user_status change.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from itsdangerous import BadSignature, URLSafeSerializer

from app.config import get_settings

_SERIALIZER_SALT = "vkma-bearer-v1"


def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(get_settings().session_secret_key, salt=_SERIALIZER_SALT)


def sign_bearer(internal_user_id: int, vk_user_id: int) -> str:
    return _serializer().dumps({"uid": internal_user_id, "vku": vk_user_id})


def verify_bearer(token: str) -> dict[str, Any] | None:
    try:
        data = _serializer().loads(token)
    except BadSignature:
        return None
    if not isinstance(data, dict) or "uid" not in data:
        return None
    return data


def get_bearer_user_id(request: Request) -> int | None:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    data = verify_bearer(header[7:].strip())
    if data is None:
        return None
    return int(data["uid"])
