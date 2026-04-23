"""HTTP Basic admin authentication for the single-coordinator MVP."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import get_settings

security = HTTPBasic(auto_error=False)


def require_admin(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(security)],
) -> str:
    settings = get_settings()
    if not settings.admin_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ADMIN_PASSWORD is not configured",
        )
    if credentials is None:
        raise _unauthorized()
    login_ok = secrets.compare_digest(credentials.username, "admin")
    password_ok = secrets.compare_digest(credentials.password, settings.admin_password)
    if not (login_ok and password_ok):
        raise _unauthorized()
    return credentials.username


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Admin authentication required",
        headers={"WWW-Authenticate": "Basic"},
    )
