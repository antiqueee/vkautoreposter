"""Public-facing routes: landing and participant cabinet."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, select, update

from app.audit import record as audit_record
from app.auth.sessions import get_current_user_id
from app.crypto import encrypt_token
from app.db import connection
from app.models import Actor, TaskStatus, UserStatus, audit_log, posts, repost_tasks, users

router = APIRouter()

_TEMPLATES = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates"),
)


@router.get("/")
async def landing(request: Request) -> Response:
    if get_current_user_id(request) is not None:
        return RedirectResponse(url="/me", status_code=302)
    return _TEMPLATES.TemplateResponse(request, "landing.html", {})


@router.get("/me")
async def me(request: Request) -> Response:
    uid = get_current_user_id(request)
    if uid is None:
        return RedirectResponse(url="/", status_code=302)
    with connection() as conn:
        row = conn.execute(
            select(
                users.c.id,
                users.c.vk_user_id,
                users.c.display_name,
                users.c.status,
                users.c.consented_at,
                users.c.token_verified_at,
            ).where(users.c.id == uid)
        ).first()
        history = conn.execute(
            select(
                repost_tasks.c.id,
                repost_tasks.c.status,
                repost_tasks.c.scheduled_at,
                repost_tasks.c.finished_at,
                repost_tasks.c.error_code,
                posts.c.url,
                posts.c.text_preview,
            )
            .join(posts, posts.c.id == repost_tasks.c.post_id)
            .where(repost_tasks.c.user_id == uid)
            .order_by(desc(repost_tasks.c.scheduled_at))
            .limit(50)
        ).mappings().all()
        audit_rows = conn.execute(
            select(audit_log.c.action, audit_log.c.created_at, audit_log.c.details_json)
            .where(audit_log.c.user_id == uid)
            .order_by(desc(audit_log.c.created_at))
            .limit(20)
        ).mappings().all()
    if row is None:
        request.session.clear()
        return RedirectResponse(url="/", status_code=302)
    return _TEMPLATES.TemplateResponse(
        request,
        "me.html",
        {"user": row._mapping, "history": history, "audit_rows": audit_rows},
    )


@router.post("/me/pause")
async def pause_me(request: Request) -> Response:
    uid = get_current_user_id(request)
    if uid is None:
        return RedirectResponse(url="/", status_code=302)
    now = datetime.utcnow()
    with connection() as conn:
        conn.execute(
            update(users).where(users.c.id == uid).values(status=UserStatus.PAUSED, updated_at=now)
        )
        audit_record(conn, actor=Actor.USER, action="pause", user_id=uid)
    return RedirectResponse(url="/me", status_code=303)


@router.post("/me/resume")
async def resume_me(request: Request) -> Response:
    uid = get_current_user_id(request)
    if uid is None:
        return RedirectResponse(url="/", status_code=302)
    now = datetime.utcnow()
    with connection() as conn:
        conn.execute(
            update(users).where(users.c.id == uid).values(status=UserStatus.ACTIVE, updated_at=now)
        )
        audit_record(conn, actor=Actor.USER, action="resume", user_id=uid)
    return RedirectResponse(url="/me", status_code=303)


@router.post("/me/revoke")
async def revoke_me(request: Request) -> Response:
    uid = get_current_user_id(request)
    if uid is None:
        return RedirectResponse(url="/", status_code=302)
    now = datetime.utcnow()
    with connection() as conn:
        conn.execute(
            update(users)
            .where(users.c.id == uid)
            .values(
                status=UserStatus.REVOKED,
                encrypted_token=encrypt_token("revoked"),
                updated_at=now,
            )
        )
        conn.execute(
            update(repost_tasks)
            .where(repost_tasks.c.user_id == uid)
            .where(repost_tasks.c.status == TaskStatus.PENDING)
            .values(status=TaskStatus.CANCELLED, finished_at=now, error_code="user_revoked")
        )
        audit_record(conn, actor=Actor.USER, action="revoke", user_id=uid)
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)
