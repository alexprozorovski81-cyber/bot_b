"""add achievements tables

Revision ID: 0005_add_achievements
Revises: 0004_add_withdrawals
Create Date: 2026-04-27
"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "0005_add_achievements"
down_revision: Union[str, None] = "0004_add_withdrawals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "achievements",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("slug", sa.String(64), unique=True, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("emoji", sa.String(8), nullable=False),
        sa.Column("description", sa.String(256), nullable=False),
        sa.Column("condition_type", sa.String(32), nullable=False),
        sa.Column("condition_value", sa.Integer, nullable=False, server_default="1"),
        sa.Column("rarity", sa.String(16), nullable=False, server_default="common"),
    )
    op.create_table(
        "user_achievements",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("achievement_id", sa.Integer, sa.ForeignKey("achievements.id"), nullable=False),
        sa.Column("unlocked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_user_achievements_user_id", "user_achievements", ["user_id"])
    op.create_index("ix_user_achievements_achievement_id", "user_achievements", ["achievement_id"])


def downgrade() -> None:
    op.drop_index("ix_user_achievements_achievement_id", "user_achievements")
    op.drop_index("ix_user_achievements_user_id", "user_achievements")
    op.drop_table("user_achievements")
    op.drop_table("achievements")
