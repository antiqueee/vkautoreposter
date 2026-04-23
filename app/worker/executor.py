"""Claim due repost tasks and execute them against VK."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update

from app.audit import record as audit_record
from app.config import get_settings
from app.crypto import decrypt_token
from app.db import connection, get_engine
from app.models import Actor, TaskStatus, UserStatus, posts, repost_tasks, users
from app.vk.api import VkApiError, VkErrorCode, VkNetworkError, wall_repost

_log = logging.getLogger("app.worker.executor")

_RETRYABLE_VK_CODES = {VkErrorCode.TOO_MANY_REQUESTS, VkErrorCode.RATE_LIMIT}


async def run_due_tasks() -> int:
    """Claim and execute one batch. Returns number of tasks claimed."""
    claimed = _claim_due_tasks()
    if not claimed:
        return 0
    for task in claimed:
        await _execute_one(task)
    return len(claimed)


def _claim_due_tasks() -> list[dict[str, Any]]:
    settings = get_settings()
    now = datetime.utcnow()
    with get_engine().connect() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        rows = conn.execute(
            select(repost_tasks.c.id)
            .where(repost_tasks.c.status == TaskStatus.PENDING)
            .where(repost_tasks.c.scheduled_at <= now)
            .order_by(repost_tasks.c.scheduled_at)
            .limit(settings.worker_batch_size)
        ).all()
        ids = [int(row.id) for row in rows]
        if not ids:
            conn.rollback()
            return []
        conn.execute(
            update(repost_tasks)
            .where(repost_tasks.c.id.in_(ids))
            .values(status=TaskStatus.RUNNING, started_at=now, error_code=None, error_details=None)
        )
        claimed = conn.execute(
            select(
                repost_tasks.c.id,
                repost_tasks.c.user_id,
                repost_tasks.c.post_id,
                repost_tasks.c.scheduled_at,
                repost_tasks.c.retry_count,
            ).where(repost_tasks.c.id.in_(ids))
        ).mappings().all()
        conn.commit()
    return [dict(row) for row in claimed]


async def _execute_one(task: dict[str, Any]) -> None:
    settings = get_settings()
    now = datetime.utcnow()
    missed_after = timedelta(seconds=settings.task_missed_threshold_seconds)

    with connection() as conn:
        row = conn.execute(
            select(
                repost_tasks.c.id,
                repost_tasks.c.scheduled_at,
                repost_tasks.c.retry_count,
                users.c.id.label("user_id"),
                users.c.status.label("user_status"),
                users.c.encrypted_token,
                posts.c.vk_owner_id,
                posts.c.vk_post_id,
            )
            .join(users, users.c.id == repost_tasks.c.user_id)
            .join(posts, posts.c.id == repost_tasks.c.post_id)
            .where(repost_tasks.c.id == int(task["id"]))
        ).mappings().first()
        if row is None:
            _log.warning("claimed task disappeared id=%s", task["id"])
            return

        if row["user_status"] != UserStatus.ACTIVE:
            status = (
                TaskStatus.SKIPPED_PAUSED
                if row["user_status"] == UserStatus.PAUSED
                else TaskStatus.CANCELLED
            )
            conn.execute(
                update(repost_tasks)
                .where(repost_tasks.c.id == row["id"])
                .values(status=status, finished_at=now)
            )
            audit_record(
                conn,
                actor=Actor.SYSTEM,
                action="repost_skipped_user_inactive",
                user_id=int(row["user_id"]),
                details={"task_id": row["id"], "user_status": row["user_status"]},
            )
            return

        if now - row["scheduled_at"] > missed_after:
            conn.execute(
                update(repost_tasks)
                .where(repost_tasks.c.id == row["id"])
                .values(status=TaskStatus.MISSED, finished_at=now, error_code="missed_window")
            )
            audit_record(
                conn,
                actor=Actor.SYSTEM,
                action="repost_missed",
                user_id=int(row["user_id"]),
                details={"task_id": row["id"]},
            )
            return

        token = decrypt_token(row["encrypted_token"])
        object_id = f"wall{row['vk_owner_id']}_{row['vk_post_id']}"

    try:
        await wall_repost(object_id=object_id, access_token=token)
    except VkNetworkError as exc:
        _retry_or_fail(
            task_id=int(task["id"]),
            user_id=int(row["user_id"]),
            code="network",
            details=str(exc),
        )
        return
    except VkApiError as exc:
        _handle_vk_error(task_id=int(task["id"]), user_id=int(row["user_id"]), exc=exc)
        return

    with connection() as conn:
        done_at = datetime.utcnow()
        conn.execute(
            update(repost_tasks)
            .where(repost_tasks.c.id == int(task["id"]))
            .values(status=TaskStatus.DONE, finished_at=done_at)
        )
        conn.execute(
            update(users)
            .where(users.c.id == int(row["user_id"]))
            .values(token_verified_at=done_at, updated_at=done_at)
        )
        audit_record(
            conn,
            actor=Actor.SYSTEM,
            action="repost_ok",
            user_id=int(row["user_id"]),
            details={"task_id": task["id"], "object": object_id},
        )


def _retry_or_fail(*, task_id: int, user_id: int, code: str, details: str) -> None:
    now = datetime.utcnow()
    with connection() as conn:
        task = conn.execute(
            select(repost_tasks.c.retry_count).where(repost_tasks.c.id == task_id)
        ).one()
        retry_count = int(task.retry_count)
        if retry_count < 3:
            delay = [60, 300, 1200][retry_count]
            conn.execute(
                update(repost_tasks)
                .where(repost_tasks.c.id == task_id)
                .values(
                    status=TaskStatus.PENDING,
                    scheduled_at=now + timedelta(seconds=delay),
                    retry_count=retry_count + 1,
                    error_code=code,
                    error_details=details[:1000],
                )
            )
            action = "repost_retry_scheduled"
        else:
            conn.execute(
                update(repost_tasks)
                .where(repost_tasks.c.id == task_id)
                .values(
                    status=TaskStatus.FAILED,
                    finished_at=now,
                    error_code=code,
                    error_details=details[:1000],
                )
            )
            action = "repost_failed"
        audit_record(
            conn,
            actor=Actor.SYSTEM,
            action=action,
            user_id=user_id,
            details={"task_id": task_id, "error_code": code},
        )


def _handle_vk_error(*, task_id: int, user_id: int, exc: VkApiError) -> None:
    code = exc.error_code
    if code in _RETRYABLE_VK_CODES:
        _retry_or_fail(task_id=task_id, user_id=user_id, code=f"vk_{code}", details=exc.error_msg)
        return

    now = datetime.utcnow()
    user_status: str | None = None
    task_status = TaskStatus.FAILED
    error_code = f"vk_{code}"
    if code in {
        VkErrorCode.AUTH_FAILED,
        VkErrorCode.CAPTCHA_REQUIRED,
        VkErrorCode.VALIDATION_REQUIRED,
    }:
        user_status = UserStatus.INVALID_TOKEN
        task_status = TaskStatus.CANCELLED
    elif code == VkErrorCode.USER_DELETED_OR_BANNED:
        user_status = UserStatus.REVOKED
        task_status = TaskStatus.CANCELLED

    with connection() as conn:
        conn.execute(
            update(repost_tasks)
            .where(repost_tasks.c.id == task_id)
            .values(
                status=task_status,
                finished_at=now,
                error_code=error_code,
                error_details=exc.error_msg[:1000],
            )
        )
        if user_status:
            conn.execute(
                update(users)
                .where(users.c.id == user_id)
                .values(status=user_status, updated_at=now)
            )
        audit_record(
            conn,
            actor=Actor.SYSTEM,
            action="repost_vk_error",
            user_id=user_id,
            details={"task_id": task_id, "vk_code": code, "user_status": user_status},
        )
