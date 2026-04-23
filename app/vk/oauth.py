"""Minimal auth helpers for VK ID flow."""

from __future__ import annotations

import secrets


def generate_state() -> str:
    """CSRF token stored in a signed session cookie and echoed back on POST."""
    return secrets.token_urlsafe(24)
