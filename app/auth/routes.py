"""Participant auth routes.

Current flow:
  GET  /auth/vk/start       → generate CSRF state and redirect to the auth page
  GET  /auth/vk/complete    → serve VK ID SDK page
  POST /auth/vk/complete    → accept auth code from the SDK, exchange it on the
                              server, verify the resulting token via users.get,
                              upsert users row, set session
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
    pop_oauth_code_verifier,
    pop_oauth_state,
    set_current_user,
    set_oauth_code_verifier,
    set_oauth_state,
)
from app.config import get_settings
from app.crypto import encrypt_token
from app.db import connection
from app.models import Actor, UserStatus, users
from app.vk.api import VkApiError, VkNetworkError, users_get
from app.vk.oauth import (
    VkIdExchangeError,
    exchange_code_for_access_token,
    generate_code_challenge,
    generate_code_verifier,
    generate_state,
)

_log = logging.getLogger("app.auth.routes")

router = APIRouter(prefix="/auth/vk", tags=["auth"])

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/start")
async def start(request: Request) -> RedirectResponse:
    """Begin auth: generate CSRF state and send the participant to the VK ID page."""
    if get_current_user_id(request) is not None:
        return RedirectResponse(url="/me", status_code=302)
    state = generate_state()
    set_oauth_state(request, state)
    return RedirectResponse(url="/auth/vk/complete", status_code=302)


@router.get("/complete", response_class=HTMLResponse)
async def complete_get(request: Request) -> HTMLResponse:
    """Serve the VK ID SDK page."""
    expected_state = request.session.get("vk_oauth_state")
    code_verifier = request.session.get("vk_oauth_code_verifier")
    if not expected_state or not code_verifier:
        state = generate_state()
        code_verifier = generate_code_verifier()
        set_oauth_state(request, state)
        set_oauth_code_verifier(request, code_verifier)
        expected_state = state

    return _TEMPLATES.TemplateResponse(
        request,
        "auth/complete.html",
        {
            "post_url": "/auth/vk/complete",
            "vk_app_id": get_settings().vk_app_id,
            "vk_scope": get_settings().vk_oauth_scope,
            "redirect_url": get_settings().oauth_redirect_uri,
            "vk_code_challenge": generate_code_challenge(code_verifier),
            "state": expected_state,
        },
    )


@router.post("/complete")
async def complete_post(
    request: Request,
    code: Annotated[str, Form()],
    device_id: Annotated[str, Form()],
    state: Annotated[str, Form()],
) -> RedirectResponse:
    expected_state = pop_oauth_state(request)
    code_verifier = pop_oauth_code_verifier(request)
    if not expected_state or expected_state != state or not code_verifier:
        _log.warning("oauth state mismatch")
        raise HTTPException(status_code=400, detail="Auth state mismatch. Try again.")

    try:
        access_token = await exchange_code_for_access_token(
            client_id=get_settings().vk_app_id,
            redirect_uri=get_settings().oauth_redirect_uri,
            code=code,
            device_id=device_id,
            code_verifier=code_verifier,
            state=state,
        )
    except VkIdExchangeError as exc:
        _log.warning("vk id code exchange failed: %s", exc)
        raise HTTPException(status_code=400, detail="VK ID code exchange failed") from exc

    try:
        profiles = await users_get(access_token)
    except VkApiError as exc:
        _log.warning("users.get rejected token: code=%s msg=%s", exc.error_code, exc.error_msg)
        raise HTTPException(status_code=400, detail="VK rejected the token") from exc
    except VkNetworkError as exc:
        _log.warning("users.get network error: %s", exc)
        raise HTTPException(status_code=502, detail="VK unreachable. Retry.") from exc

    if not profiles:
        raise HTTPException(status_code=400, detail="VK returned empty profile")
    profile = profiles[0]
    user_id = int(profile["id"])
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
