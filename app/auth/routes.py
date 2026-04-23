"""Participant OAuth routes.

Flow:
  GET  /auth/vk/start       → generate state, store in session, 302 to VK
  GET  /auth/vk/complete    → HTML page with JS that reads the token from the
                              URL fragment and POSTs it to the next endpoint
  POST /auth/vk/complete    → verify state, verify token via users.get,
                              upsert users row with encrypted token, set session
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import insert, select, update

from app.audit import record as audit_record
from app.auth.sessions import (
    get_current_user_id,
    pop_oauth_state,
    set_current_user,
    set_oauth_state,
)
from app.crypto import encrypt_token
from app.db import connection
from app.models import Actor, UserStatus, users
from app.vk.api import VkApiError, VkNetworkError, users_get
from app.vk.oauth import build_auth_url, generate_state

_log = logging.getLogger("app.auth.routes")

router = APIRouter(prefix="/auth/vk", tags=["auth"])

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/start")
async def start(request: Request) -> RedirectResponse:
    """Begin OAuth: generate CSRF state, persist in session, 302 to VK authorize."""
    if get_current_user_id(request) is not None:
        return RedirectResponse(url="/me", status_code=302)
    state = generate_state()
    set_oauth_state(request, state)
    return RedirectResponse(url=build_auth_url(state), status_code=302)


@router.get("/complete", response_class=HTMLResponse)
async def complete_get(request: Request) -> HTMLResponse:
    """Serve the fragment-reader page.

    VK redirects here with the token in the URL fragment (``#access_token=...``).
    Fragments never reach the server, so we render a page whose JS reads the
    fragment, then POSTs it to this same path.
    """
    return _TEMPLATES.TemplateResponse(
        request,
        "auth/complete.html",
        {"post_url": "/auth/vk/complete"},
    )


@router.post("/complete")
async def complete_post(
    request: Request,
    access_token: Annotated[str, Form()],
    user_id: Annotated[int, Form()],
    state: Annotated[str, Form()],
    expires_in: Annotated[int, Form()] = 0,
) -> RedirectResponse:
    expected_state = pop_oauth_state(request)
    if not expected_state or expected_state != state:
        _log.warning("oauth state mismatch")
        raise HTTPException(status_code=400, detail="OAuth state mismatch. Try again.")

    try:
        profiles = await users_get(access_token, user_ids=[user_id])
    except VkApiError as exc:
        _log.warning("users.get rejected token: code=%s msg=%s", exc.error_code, exc.error_msg)
        raise HTTPException(status_code=400, detail="VK rejected the token") from exc
    except VkNetworkError as exc:
        _log.warning("users.get network error: %s", exc)
        raise HTTPException(status_code=502, detail="VK unreachable. Retry.") from exc

    if not profiles:
        raise HTTPException(status_code=400, detail="VK returned empty profile")
    profile = profiles[0]
    display_name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip() \
        or profile.get("screen_name") or f"id{user_id}"

    encrypted = encrypt_token(access_token)
    now = datetime.utcnow()
    ip = request.client.host if request.client else None

    with connection() as conn:
        existing = conn.execute(
            select(users.c.id, users.c.status).where(users.c.vk_user_id == user_id)
        ).first()
        if existing is None:
            result = conn.execute(
                insert(users).values(
                    vk_user_id=user_id,
                    display_name=display_name,
                    status=UserStatus.ACTIVE,
                    encrypted_token=encrypted,
                    token_verified_at=now,
                    consented_at=now,
                )
            )
            internal_id = int(result.inserted_primary_key[0])
            audit_record(
                conn,
                actor=Actor.USER,
                action="oauth_onboard",
                user_id=internal_id,
                details={"vk_user_id": user_id, "display_name": display_name},
                ip_address=ip,
            )
        else:
            internal_id = int(existing.id)
            conn.execute(
                update(users)
                .where(users.c.id == internal_id)
                .values(
                    display_name=display_name,
                    encrypted_token=encrypted,
                    token_verified_at=now,
                    status=UserStatus.ACTIVE,
                    updated_at=now,
                )
            )
            audit_record(
                conn,
                actor=Actor.USER,
                action="oauth_refresh",
                user_id=internal_id,
                details={"vk_user_id": user_id, "previous_status": existing.status},
                ip_address=ip,
            )

    set_current_user(request, internal_id)
    return RedirectResponse(url="/me", status_code=303)


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    """Terminate the session (does NOT revoke the token — that's /me/revoke)."""
    uid = get_current_user_id(request)
    if uid is not None:
        with connection() as conn:
            audit_record(conn, actor=Actor.USER, action="logout", user_id=uid)
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)
