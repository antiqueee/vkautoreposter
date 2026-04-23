"""Coordinator admin UI."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import case, desc, func, select, update

from app.audit import record as audit_record
from app.auth.admin import require_admin
from app.config import get_settings
from app.db import connection
from app.models import (
    Actor,
    TaskStatus,
    UserStatus,
    audit_log,
    posts,
    repost_tasks,
    source_config,
    users,
)
from app.worker.poller import enqueue_manual_post

router = APIRouter(prefix="/admin", tags=["admin"])

_TEMPLATES = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates"),
)


@router.get("")
async def dashboard(request: Request, _admin: Annotated[str, Depends(require_admin)]):
    settings = get_settings()
    since = datetime.utcnow() - timedelta(hours=24)
    with connection() as conn:
        user_rows = conn.execute(
            select(
                users.c.id,
                users.c.vk_user_id,
                users.c.display_name,
                users.c.status,
                users.c.token_verified_at,
                func.count(repost_tasks.c.id).label("task_count"),
                func.sum(case((repost_tasks.c.status == TaskStatus.DONE, 1), else_=0)).label(
                    "done_count"
                ),
                func.sum(case((repost_tasks.c.status == TaskStatus.FAILED, 1), else_=0)).label(
                    "failed_count"
                ),
            )
            .outerjoin(repost_tasks, repost_tasks.c.user_id == users.c.id)
            .group_by(users.c.id)
            .order_by(users.c.display_name)
        ).mappings().all()
        post_rows = conn.execute(
            select(
                posts.c.id,
                posts.c.url,
                posts.c.published_at,
                posts.c.detected_at,
                func.count(repost_tasks.c.id).label("task_count"),
                func.sum(case((repost_tasks.c.status == TaskStatus.DONE, 1), else_=0)).label(
                    "done_count"
                ),
                func.sum(case((repost_tasks.c.status == TaskStatus.FAILED, 1), else_=0)).label(
                    "failed_count"
                ),
            )
            .outerjoin(repost_tasks, repost_tasks.c.post_id == posts.c.id)
            .group_by(posts.c.id)
            .order_by(desc(posts.c.detected_at))
            .limit(20)
        ).mappings().all()
        failed_rows = conn.execute(
            select(
                repost_tasks.c.id,
                repost_tasks.c.error_code,
                repost_tasks.c.error_details,
                repost_tasks.c.finished_at,
                users.c.display_name,
                users.c.vk_user_id,
                posts.c.url,
            )
            .join(users, users.c.id == repost_tasks.c.user_id)
            .join(posts, posts.c.id == repost_tasks.c.post_id)
            .where(repost_tasks.c.status == TaskStatus.FAILED)
            .where(repost_tasks.c.finished_at >= since)
            .order_by(desc(repost_tasks.c.finished_at))
            .limit(50)
        ).mappings().all()
        audit_rows = conn.execute(
            select(audit_log.c.actor, audit_log.c.action, audit_log.c.details_json, audit_log.c.created_at)
            .where(audit_log.c.actor.in_([Actor.ADMIN, Actor.SYSTEM]))
            .order_by(desc(audit_log.c.created_at))
            .limit(20)
        ).mappings().all()
        cfg = conn.execute(select(source_config).where(source_config.c.id == 1)).mappings().first()
    return _TEMPLATES.TemplateResponse(
        request,
        "admin/dashboard.html",
        {
            "users": user_rows,
            "posts": post_rows,
            "failed": failed_rows,
            "audit_rows": audit_rows,
            "source": cfg,
            "settings": settings,
        },
    )


@router.post("/users/{user_id}/disable")
async def disable_user(
    user_id: int,
    _admin: Annotated[str, Depends(require_admin)],
) -> RedirectResponse:
    now = datetime.utcnow()
    with connection() as conn:
        conn.execute(
            update(users)
            .where(users.c.id == user_id)
            .values(status=UserStatus.PAUSED, updated_at=now)
        )
        audit_record(conn, actor=Actor.ADMIN, action="admin_pause_user", user_id=user_id)
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/users/{user_id}/enable")
async def enable_user(
    user_id: int,
    _admin: Annotated[str, Depends(require_admin)],
) -> RedirectResponse:
    now = datetime.utcnow()
    with connection() as conn:
        conn.execute(
            update(users)
            .where(users.c.id == user_id)
            .values(status=UserStatus.ACTIVE, updated_at=now)
        )
        audit_record(conn, actor=Actor.ADMIN, action="admin_resume_user", user_id=user_id)
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/manual-trigger")
async def manual_trigger(
    post_url: Annotated[str, Form()],
    _admin: Annotated[str, Depends(require_admin)],
) -> RedirectResponse:
    try:
        created = enqueue_manual_post(post_url)
        action = "manual_trigger_ok" if created else "manual_trigger_duplicate"
        details = {"url": post_url, "created": created}
    except Exception as exc:  # noqa: BLE001 - admin UI should show the failure via audit/logs.
        action = "manual_trigger_failed"
        details = {"url": post_url, "error": str(exc)}
    with connection() as conn:
        audit_record(conn, actor=Actor.ADMIN, action=action, details=details)
    return RedirectResponse(url="/admin", status_code=303)
