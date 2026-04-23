"""initial: users and audit_log

Revision ID: 0001
Revises:
Create Date: 2026-04-23

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: str | None = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("vk_user_id", sa.Integer, nullable=False),
        sa.Column("display_name", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("encrypted_token", sa.LargeBinary, nullable=False),
        sa.Column("token_verified_at", sa.DateTime),
        sa.Column("consented_at", sa.DateTime, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "status IN ('active','paused','revoked','invalid_token')",
            name="users_status_check",
        ),
        sa.UniqueConstraint("vk_user_id", name="users_vk_user_id_unique"),
    )
    op.create_index("idx_users_status", "users", ["status"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("actor", sa.String, nullable=False),
        sa.Column("action", sa.String, nullable=False),
        sa.Column("details_json", sa.Text),
        sa.Column("ip_address", sa.String),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "actor IN ('user','admin','system','vk_callback')",
            name="audit_log_actor_check",
        ),
    )
    op.create_index("idx_audit_user_time", "audit_log", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_audit_user_time", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("idx_users_status", table_name="users")
    op.drop_table("users")
