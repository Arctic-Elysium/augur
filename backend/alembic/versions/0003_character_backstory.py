"""character backstory and hooks

Revision ID: 0003
Revises: 0002
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("characters", sa.Column("backstory", sa.Text(), nullable=True))
    op.add_column(
        "characters",
        sa.Column(
            "hooks", postgresql.JSONB(), nullable=False, server_default="[]"
        ),
    )


def downgrade() -> None:
    op.drop_column("characters", "hooks")
    op.drop_column("characters", "backstory")
