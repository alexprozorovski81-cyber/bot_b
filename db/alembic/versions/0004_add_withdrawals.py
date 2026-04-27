"""add withdrawals table

Revision ID: 0004_add_withdrawals
Revises: 0003_add_comments
Create Date: 2026-04-27
"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "0004_add_withdrawals"
down_revision: Union[str, None] = "0003_add_comments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "withdrawals",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("amount_coins", sa.Numeric(18, 2), nullable=False),
        sa.Column("network", sa.String(32), nullable=False),
        sa.Column("wallet_address", sa.String(256), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("admin_note", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_withdrawals_user_id", "withdrawals", ["user_id"])
    op.create_index("ix_withdrawals_status", "withdrawals", ["status"])


def downgrade() -> None:
    op.drop_index("ix_withdrawals_status", "withdrawals")
    op.drop_index("ix_withdrawals_user_id", "withdrawals")
    op.drop_table("withdrawals")
