"""add article_url to events

Revision ID: 0002_add_article_url
Revises: 0001_initial
Create Date: 2026-04-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_add_article_url"
down_revision: Union[str, None] = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("events", sa.Column("article_url", sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column("events", "article_url")
