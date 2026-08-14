"""character archival, and drop stress from sheets

Revision ID: 0008
Revises: 0007
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "characters", sa.Column("archived_reason", sa.String(20), nullable=True)
    )
    op.add_column("characters", sa.Column("epitaph", sa.Text(), nullable=True))
    # Stress is gone as a mechanic. Left in place these keys are dead weight
    # the rules engine ignores and the sheet still renders.
    op.execute("UPDATE characters SET sheet = sheet - 'stress' - 'stress_max'")


def downgrade() -> None:
    op.drop_column("characters", "epitaph")
    op.drop_column("characters", "archived_reason")
