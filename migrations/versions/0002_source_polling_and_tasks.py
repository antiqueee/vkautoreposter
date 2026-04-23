"""source polling and repost tasks

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-23

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_config",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("vk_group_id", sa.Integer, nullable=False),
        sa.Column("enabled", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("last_seen_vk_post_id", sa.Integer),
        sa.Column("last_polled_at", sa.DateTime),
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
        sa.CheckConstraint("id = 1", name="source_config_singleton_check"),
        sa.CheckConstraint("vk_group_id > 0", name="source_config_group_positive_check"),
    )

    op.create_table(
        "posts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("vk_owner_id", sa.Integer, nullable=False),
        sa.Column("vk_post_id", sa.Integer, nullable=False),
        sa.Column(
            "detected_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("published_at", sa.DateTime, nullable=False),
        sa.Column("url", sa.String, nullable=False),
        sa.Column("text_preview", sa.Text),
        sa.Column("marked_as_ads", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.UniqueConstraint("vk_owner_id", "vk_post_id", name="posts_vk_unique"),
    )

    op.create_table(
        "repost_tasks",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("post_id", sa.Integer, sa.ForeignKey("posts.id"), nullable=False),
        sa.Column("scheduled_at", sa.DateTime, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("started_at", sa.DateTime),
        sa.Column("finished_at", sa.DateTime),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("error_code", sa.String),
        sa.Column("error_details", sa.Text),
        sa.CheckConstraint(
            "status IN ("
            "'pending','running','done','failed','skipped_paused','cancelled','missed'"
            ")",
            name="repost_tasks_status_check",
        ),
        sa.UniqueConstraint("user_id", "post_id", name="repost_tasks_user_post_unique"),
    )
    op.create_index("idx_repost_tasks_due", "repost_tasks", ["status", "scheduled_at"])
    op.create_index("idx_repost_tasks_user", "repost_tasks", ["user_id"])


def downgrade() -> None:
    op.drop_index("idx_repost_tasks_user", table_name="repost_tasks")
    op.drop_index("idx_repost_tasks_due", table_name="repost_tasks")
    op.drop_table("repost_tasks")
    op.drop_table("posts")
    op.drop_table("source_config")
