"""initial check_results table

Revision ID: 0001
Revises:
Create Date: 2026-06-17

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "check_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("check_type", sa.String(length=40), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_check_results_name", "check_results", ["name"])
    op.create_index("ix_check_results_check_type", "check_results", ["check_type"])
    op.create_index("ix_check_results_status", "check_results", ["status"])
    op.create_index("ix_check_results_created_at", "check_results", ["created_at"])
    op.create_index(
        "ix_check_results_name_created", "check_results", ["name", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_check_results_name_created", table_name="check_results")
    op.drop_index("ix_check_results_created_at", table_name="check_results")
    op.drop_index("ix_check_results_status", table_name="check_results")
    op.drop_index("ix_check_results_check_type", table_name="check_results")
    op.drop_index("ix_check_results_name", table_name="check_results")
    op.drop_table("check_results")
