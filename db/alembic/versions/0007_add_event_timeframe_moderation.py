"""add event timeframe, auto_resolve fields, moderation status, user_stats table

Revision ID: 0007_add_event_timeframe_moderation
Revises: 0006_add_registration_log
Create Date: 2026-05-01
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_add_event_timeframe_moderation"
down_revision: Union[str, None] = "0006_add_registration_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # Postgres: добавить новое значение в enum eventstatus
    # SQLite хранит enum как VARCHAR — дополнительный DDL не нужен
    if is_pg:
        op.execute("ALTER TYPE eventstatus ADD VALUE IF NOT EXISTS 'moderation'")

    # batch_alter_table безопасен для обоих диалектов (SQLite + Postgres)
    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "timeframe",
                sa.String(16),
                nullable=False,
                server_default="longterm",
            )
        )
        batch_op.add_column(
            sa.Column("auto_resolve_source", sa.String(64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("auto_resolve_payload", sa.Text, nullable=True)
        )

    op.create_index("ix_events_timeframe", "events", ["timeframe"])
    op.create_index(
        "ix_events_timeframe_status", "events", ["timeframe", "status"]
    )

    op.create_table(
        "user_stats",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("period", sa.String(8), nullable=False),
        sa.Column(
            "net_profit",
            sa.Numeric(18, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "bets_count", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column(
            "win_count", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "user_id", "period", name="uq_user_stats_user_period"
        ),
    )
    op.create_index("ix_user_stats_user_id", "user_stats", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_stats_user_id", "user_stats")
    op.drop_table("user_stats")

    op.drop_index("ix_events_timeframe_status", "events")
    op.drop_index("ix_events_timeframe", "events")

    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.drop_column("auto_resolve_payload")
        batch_op.drop_column("auto_resolve_source")
        batch_op.drop_column("timeframe")

    # Postgres: нельзя удалить значение enum без пересоздания типа.
    # Downgrade для enum намеренно не реализован.
