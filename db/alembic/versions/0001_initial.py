"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("slug", sa.String(32), unique=True, nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("emoji", sa.String(8), nullable=False),
        sa.Column("sort_order", sa.Integer, default=0),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("telegram_id", sa.BigInteger, unique=True, nullable=False),
        sa.Column("username", sa.String(64)),
        sa.Column("first_name", sa.String(128)),
        sa.Column("balance_rub", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("referrer_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("referral_code", sa.String(16), unique=True, nullable=False),
        sa.Column("is_banned", sa.Boolean, server_default=sa.false()),
        sa.Column("is_admin", sa.Boolean, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_active_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_users_telegram_id", "users", ["telegram_id"])

    event_status = sa.Enum(
        "draft", "active", "locked", "resolved", "cancelled",
        name="eventstatus",
    )
    event_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("slug", sa.String(128), unique=True, nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("image_url", sa.String(512)),
        sa.Column("category_id", sa.Integer, sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("status", event_status, server_default="draft", nullable=False),
        sa.Column("liquidity_b", sa.Numeric(18, 2), server_default="1000"),
        sa.Column("closes_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolves_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("winning_outcome_id", sa.Integer),
        sa.Column("resolution_source", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_events_status_closes", "events", ["status", "closes_at"])

    op.create_table(
        "outcomes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_id", sa.Integer, sa.ForeignKey("events.id"), nullable=False),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("shares_outstanding", sa.Numeric(18, 4), server_default="0"),
        sa.Column("sort_order", sa.Integer, default=0),
    )
    op.create_index("ix_outcomes_event", "outcomes", ["event_id"])

    op.create_foreign_key(
        "fk_events_winning_outcome",
        "events", "outcomes",
        ["winning_outcome_id"], ["id"],
    )

    op.create_table(
        "bets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("event_id", sa.Integer, sa.ForeignKey("events.id"), nullable=False),
        sa.Column("outcome_id", sa.Integer, sa.ForeignKey("outcomes.id"), nullable=False),
        sa.Column("amount_rub", sa.Numeric(18, 2), nullable=False),
        sa.Column("shares", sa.Numeric(18, 4), nullable=False),
        sa.Column("avg_odds", sa.Numeric(10, 4), nullable=False),
        sa.Column("is_settled", sa.Boolean, server_default=sa.false()),
        sa.Column("payout_rub", sa.Numeric(18, 2)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_bets_user_event", "bets", ["user_id", "event_id"])

    payment_method = sa.Enum(
        "yookassa_card", "yookassa_sbp", "usdt_ton",
        name="paymentmethod",
    )
    payment_method.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("method", payment_method, nullable=False),
        sa.Column("amount_rub", sa.Numeric(18, 2), nullable=False),
        sa.Column("external_id", sa.String(128)),
        sa.Column("status", sa.String(32), server_default="pending"),
        sa.Column("is_deposit", sa.Boolean, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_payments_external", "payments", ["external_id"])
    op.create_index("ix_payments_status", "payments", ["status"])

    transaction_type = sa.Enum(
        "deposit", "withdraw", "bet_place", "bet_payout",
        "bet_refund", "fee", "bonus",
        name="transactiontype",
    )
    transaction_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("type", transaction_type, nullable=False),
        sa.Column("amount_rub", sa.Numeric(18, 2), nullable=False),
        sa.Column("balance_before", sa.Numeric(18, 2), nullable=False),
        sa.Column("balance_after", sa.Numeric(18, 2), nullable=False),
        sa.Column("bet_id", sa.Integer, sa.ForeignKey("bets.id")),
        sa.Column("payment_id", sa.Integer, sa.ForeignKey("payments.id")),
        sa.Column("description", sa.String(256)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_transactions_user_created", "transactions", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_table("transactions")
    op.drop_table("payments")
    op.drop_table("bets")
    op.drop_constraint("fk_events_winning_outcome", "events", type_="foreignkey")
    op.drop_table("outcomes")
    op.drop_table("events")
    op.drop_table("users")
    op.drop_table("categories")
    sa.Enum(name="transactiontype").drop(op.get_bind())
    sa.Enum(name="paymentmethod").drop(op.get_bind())
    sa.Enum(name="eventstatus").drop(op.get_bind())
