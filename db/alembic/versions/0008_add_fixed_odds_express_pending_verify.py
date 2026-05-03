"""add fixed_odds event type, pending_verify status, express tables

Revision ID: 0008_add_fixed_odds_express_pending_verify
Revises: 0007_add_event_timeframe_moderation
Create Date: 2026-05-02
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_add_fixed_odds_express_pending_verify"
down_revision: Union[str, None] = "0007_add_event_timeframe_moderation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    if is_pg:
        op.execute("ALTER TYPE eventstatus ADD VALUE IF NOT EXISTS 'pending_verify'")
        op.execute(
            "DO $$ BEGIN "
            "CREATE TYPE eventtype AS ENUM ('market', 'fixed_odds'); "
            "EXCEPTION WHEN duplicate_object THEN NULL; "
            "END $$"
        )

    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "event_type",
                sa.String(16),
                nullable=False,
                server_default="market",
            )
        )
        batch_op.add_column(
            sa.Column("odds_yes", sa.Numeric(6, 2), nullable=True)
        )
        batch_op.add_column(
            sa.Column("odds_no", sa.Numeric(6, 2), nullable=True)
        )

    op.create_table(
        "expresses",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("stake", sa.Numeric(18, 2), nullable=False),
        sa.Column("total_odds", sa.Numeric(10, 4), nullable=False),
        sa.Column("potential_payout", sa.Numeric(18, 2), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_expresses_user_id", "expresses", ["user_id"])
    op.create_index("ix_expresses_status", "expresses", ["status"])

    op.create_table(
        "express_legs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("express_id", sa.Integer, sa.ForeignKey("expresses.id"), nullable=False),
        sa.Column("event_id", sa.Integer, sa.ForeignKey("events.id"), nullable=False),
        sa.Column("outcome_id", sa.Integer, sa.ForeignKey("outcomes.id"), nullable=False),
        sa.Column("odds", sa.Numeric(6, 2), nullable=False),
        sa.Column("result", sa.String(16), nullable=False, server_default="pending"),
    )
    op.create_index("ix_express_legs_express_id", "express_legs", ["express_id"])
    op.create_index("ix_express_legs_event_id", "express_legs", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_express_legs_event_id", "express_legs")
    op.drop_index("ix_express_legs_express_id", "express_legs")
    op.drop_table("express_legs")

    op.drop_index("ix_expresses_status", "expresses")
    op.drop_index("ix_expresses_user_id", "expresses")
    op.drop_table("expresses")

    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.drop_column("odds_no")
        batch_op.drop_column("odds_yes")
        batch_op.drop_column("event_type")

    # Enum values cannot be removed without recreating the type — intentionally skipped.
