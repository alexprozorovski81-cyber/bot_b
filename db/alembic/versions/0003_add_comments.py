"""add comments table

Revision ID: 0003_add_comments
Revises: 0002_add_article_url
Create Date: 2026-04-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_add_comments"
down_revision: Union[str, None] = "0002_add_article_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "comments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_id", sa.Integer, sa.ForeignKey("events.id"), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("text", sa.String(500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_comments_event_id", "comments", ["event_id"])
    op.create_index("ix_comments_created_at", "comments", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_comments_created_at", "comments")
    op.drop_index("ix_comments_event_id", "comments")
    op.drop_table("comments")
