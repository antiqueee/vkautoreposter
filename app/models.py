"""Table definitions (SQLAlchemy Core).

Phase 1 scope: users + audit_log. `source_config`, `posts`, `repost_tasks`
arrive in phase 2 migrations.
"""

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
