"""Poll the configured VK source wall and enqueue repost tasks."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.audit import record as audit_record
from app.config import get_settings
from app.db import connection
from app.models import Actor, TaskStatus, UserStatus, posts, repost_tasks, source_config, users
from app.vk.api import VkApiError, VkNetworkError, wall_get
from app.worker.scheduling import sample_repost_time

_log = logging.getLogger("app.worker.poller")
_WALL_URL_RE = re.compile(r"(?:https?://)?(?:m\.)?vk\.com/wall(-?\d+)_(\d+)")


def _post_url(group_id: int, post_id: int) -> str:
    return f"https://vk.com/wall-{group_id}_{post_id}"


def _preview(text: str | None) -> str | None:
    if not text:
        return None
    return text[:500]


def parse_wall_url(url: str) -> tuple[int, int]:
    """Parse VK wall URL and return (owner_id, post_id)."""
    match = _WALL_URL_RE.search(url.strip())
    if not match:
        raise ValueError("expected VK wall URL like https://vk.com/wall-123_456")
    return int(match.group(1)), int(match.group(2))


def ensure_source_config() -> None:
    """Create/update singleton config from ENV until phase 3 admin UI exists."""
    settings = get_settings()
    if settings.vk_source_group_id is None:
        return
    now = datetime.utcnow()
    with connection() as conn:
        stmt = sqlite_insert(source_config).values(
            id=1,
            vk_group_id=settings.vk_source_group_id,
            enabled=1,
            updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[source_config.c.id],
            set_={"vk_group_id": settings.vk_source_group_id, "updated_at": now},
        )
        conn.execute(stmt)


async def poll_source_once() -> int:
    """Fetch recent source posts and enqueue tasks. Returns number of new posts."""
    ensure_source_config()
    settings = get_settings()
    if settings.vk_source_group_id is None:
        _log.info("source polling skipped: VK_SOURCE_GROUP_ID is empty")
        return 0

    owner_id = -settings.vk_source_group_id
    try:
        wall = await wall_get(
            owner_id=owner_id,
            count=10,
            access_token=settings.vk_service_token,
        )
    except (VkApiError, VkNetworkError) as exc:
        with connection() as conn:
            audit_record(
                conn,
                actor=Actor.SYSTEM,
                action="source_poll_failed",
                details={"error": str(exc)},
            )
        raise

    items = [item for item in wall.get("items", []) if isinstance(item, dict) and "id" in item]
    if not items:
        _mark_polled()
        return 0

    now = datetime.utcnow()
    max_seen = max(int(item["id"]) for item in items)

    with connection() as conn:
        cfg = conn.execute(select(source_config).where(source_config.c.id == 1)).first()
        last_seen = int(cfg.last_seen_vk_post_id) if cfg and cfg.last_seen_vk_post_id else None

        # First poll establishes the cursor and intentionally avoids backfilling
        # historical posts. Manual trigger in phase 3 can enqueue an old URL.
        if last_seen is None:
            conn.execute(
                update(source_config)
                .where(source_config.c.id == 1)
                .values(last_seen_vk_post_id=max_seen, last_polled_at=now, updated_at=now)
            )
            audit_record(
                conn,
                actor=Actor.SYSTEM,
                action="source_cursor_initialized",
                details={"last_seen_vk_post_id": max_seen},
            )
            return 0

    new_items = sorted(
        [item for item in items if int(item.get("id", 0)) > last_seen],
        key=lambda item: int(item["id"]),
    )
    if not new_items:
        _mark_polled(max_seen=max(last_seen, max_seen))
        return 0

    created = 0
    with connection() as conn:
        for item in new_items:
            created += _enqueue_post(conn=conn, group_id=settings.vk_source_group_id, item=item)
        conn.execute(
            update(source_config)
            .where(source_config.c.id == 1)
            .values(
                last_seen_vk_post_id=max(max_seen, last_seen),
                last_polled_at=now,
                updated_at=now,
            )
        )
        audit_record(
            conn,
            actor=Actor.SYSTEM,
            action="source_poll_ok",
            details={"new_posts": created, "last_seen_vk_post_id": max(max_seen, last_seen)},
        )
    return created


def _mark_polled(*, max_seen: int | None = None) -> None:
    now = datetime.utcnow()
    with connection() as conn:
        values: dict[str, Any] = {"last_polled_at": now, "updated_at": now}
        if max_seen is not None:
            values["last_seen_vk_post_id"] = max_seen
        conn.execute(update(source_config).where(source_config.c.id == 1).values(**values))


def _enqueue_post(*, conn, group_id: int, item: dict[str, Any]) -> int:
    post_id = int(item["id"])
    owner_id = int(item.get("owner_id") or -group_id)
    published_at = datetime.utcfromtimestamp(int(item.get("date") or datetime.utcnow().timestamp()))
    base = max(published_at, datetime.utcnow())

    result = conn.execute(
        sqlite_insert(posts)
        .values(
            vk_owner_id=owner_id,
            vk_post_id=post_id,
            published_at=published_at,
            url=_post_url(group_id, post_id),
            text_preview=_preview(item.get("text")),
            marked_as_ads=int(item.get("marked_as_ads") or 0),
        )
        .on_conflict_do_nothing(index_elements=[posts.c.vk_owner_id, posts.c.vk_post_id])
    )
    if result.rowcount == 0:
        return 0

    row = conn.execute(
        select(posts.c.id).where(posts.c.vk_owner_id == owner_id, posts.c.vk_post_id == post_id)
    ).one()
    internal_post_id = int(row.id)
    active_users = conn.execute(select(users.c.id).where(users.c.status == UserStatus.ACTIVE)).all()

    for user in active_users:
        conn.execute(
            sqlite_insert(repost_tasks)
            .values(
                user_id=int(user.id),
                post_id=internal_post_id,
                scheduled_at=sample_repost_time(base),
                status=TaskStatus.PENDING,
            )
            .on_conflict_do_nothing(index_elements=[repost_tasks.c.user_id, repost_tasks.c.post_id])
        )

    audit_record(
        conn,
        actor=Actor.SYSTEM,
        action="post_enqueued",
        details={
            "vk_post_id": post_id,
            "tasks": len(active_users),
            "url": _post_url(group_id, post_id),
        },
    )
    return 1


def enqueue_manual_post(url: str) -> int:
    """Create repost tasks for a manually supplied source post URL.

    We intentionally do not fetch the post body here. The admin is explicitly
    triggering a known URL; phase 4 can add VK-side validation if needed.
    """
    settings = get_settings()
    if settings.vk_source_group_id is None:
        raise RuntimeError("VK_SOURCE_GROUP_ID is required for manual trigger")
    owner_id, post_id = parse_wall_url(url)
    expected_owner_id = -settings.vk_source_group_id
    if owner_id != expected_owner_id:
        raise ValueError(f"post owner must be wall{expected_owner_id}, got wall{owner_id}")
    item = {
        "id": post_id,
        "owner_id": owner_id,
        "date": int(datetime.utcnow().timestamp()),
        "text": None,
        "marked_as_ads": 0,
    }
    with connection() as conn:
        return _enqueue_post(conn=conn, group_id=settings.vk_source_group_id, item=item)
