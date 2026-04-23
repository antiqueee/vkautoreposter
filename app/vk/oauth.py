"""VK OAuth Implicit Flow URL builder.

Implicit flow returns the access_token in the URL fragment; the server never
sees it in any request query, so it cannot leak into reverse-proxy access logs.
The client-side JS in /auth/vk/complete reads the fragment and POSTs the token
back to our backend.
"""

from __future__ import annotations

import secrets
from urllib.parse import urlencode

from app.config import get_settings

VK_AUTHORIZE_URL = "https://oauth.vk.com/authorize"


def generate_state() -> str:
    """CSRF token stored in a signed cookie and echoed back via VK state param."""
    return secrets.token_urlsafe(24)


def build_auth_url(state: str) -> str:
    s = get_settings()
    query = urlencode(
        {
            "client_id": s.vk_app_id,
            "display": "page",
            "redirect_uri": s.oauth_redirect_uri,
            "scope": s.vk_oauth_scope,
            "response_type": "token",
            "v": s.vk_api_version,
            "state": state,
            "revoke": 1,
        }
    )
    return f"{VK_AUTHORIZE_URL}?{query}"
