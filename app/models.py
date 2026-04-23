"""Table definitions (SQLAlchemy Core)."""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)

from app.db import metadata


class UserStatus:
    ACTIVE = "active"
    PAUSED = "paused"
    REVOKED = "revoked"
    INVALID_TOKEN = "invalid_token"

    ALL = frozenset({ACTIVE, PAUSED, REVOKED, INVALID_TOKEN})


class Actor:
    USER = "user"
    ADMIN = "admin"
    SYSTEM = "system"
    VK_CALLBACK = "vk_callback"


class TaskStatus:
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED_PAUSED = "skipped_paused"
    CANCELLED = "cancelled"
    MISSED = "missed"

    ALL = frozenset({PENDING, RUNNING, DONE, FAILED, SKIPPED_PAUSED, CANCELLED, MISSED})


users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("vk_user_id", Integer, nullable=False),
    Column("display_name", String, nullable=False),
    Column("status", String, nullable=False),
    Column("encrypted_token", LargeBinary, nullable=False),
    Column("token_verified_at", DateTime),
    Column("consented_at", DateTime, nullable=False),
    Column(
        "created_at",
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column(
        "updated_at",
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    CheckConstraint(
        "status IN ('active','paused','revoked','invalid_token')",
        name="users_status_check",
    ),
    UniqueConstraint("vk_user_id", name="users_vk_user_id_unique"),
)
Index("idx_users_status", users.c.status)


audit_log = Table(
    "audit_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, ForeignKey("users.id")),
    Column("actor", String, nullable=False),
    Column("action", String, nullable=False),
    Column("details_json", Text),
    Column("ip_address", String),
    Column(
        "created_at",
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    CheckConstraint(
        "actor IN ('user','admin','system','vk_callback')",
        name="audit_log_actor_check",
    ),
)
Index("idx_audit_user_time", audit_log.c.user_id, audit_log.c.created_at)


source_config = Table(
    "source_config",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("vk_group_id", Integer, nullable=False),
    Column("enabled", Integer, nullable=False, server_default=text("1")),
    Column("last_seen_vk_post_id", Integer),
    Column("last_polled_at", DateTime),
    Column(
        "created_at",
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column(
        "updated_at",
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    CheckConstraint("id = 1", name="source_config_singleton_check"),
    CheckConstraint("vk_group_id > 0", name="source_config_group_positive_check"),
)


posts = Table(
    "posts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("vk_owner_id", Integer, nullable=False),
    Column("vk_post_id", Integer, nullable=False),
    Column(
        "detected_at",
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column("published_at", DateTime, nullable=False),
    Column("url", String, nullable=False),
    Column("text_preview", Text),
    Column("marked_as_ads", Integer, nullable=False, server_default=text("0")),
    UniqueConstraint("vk_owner_id", "vk_post_id", name="posts_vk_unique"),
)


repost_tasks = Table(
    "repost_tasks",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("post_id", Integer, ForeignKey("posts.id"), nullable=False),
    Column("scheduled_at", DateTime, nullable=False),
    Column("status", String, nullable=False),
    Column("started_at", DateTime),
    Column("finished_at", DateTime),
    Column("retry_count", Integer, nullable=False, server_default=text("0")),
    Column("error_code", String),
    Column("error_details", Text),
    CheckConstraint(
        "status IN ("
        "'pending','running','done','failed','skipped_paused','cancelled','missed'"
        ")",
        name="repost_tasks_status_check",
    ),
    UniqueConstraint("user_id", "post_id", name="repost_tasks_user_post_unique"),
)
Index("idx_repost_tasks_due", repost_tasks.c.status, repost_tasks.c.scheduled_at)
Index("idx_repost_tasks_user", repost_tasks.c.user_id)
