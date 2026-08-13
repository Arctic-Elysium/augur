"""backfill campaigns created with the placeholder ruleset

Revision ID: 0007
Revises: 0006

`core` was a placeholder written into the campaign model in Milestone 0, before
the ruleset registry existed and named the system `d20`. Campaigns created
before the default was corrected are unusable - every character creation fails
with "unknown ruleset: core", which surfaces as a 422 far from the cause.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE campaigns SET ruleset_id = 'd20' "
        "WHERE ruleset_id IS NULL OR ruleset_id NOT IN ('d20')"
    )
    op.execute(
        "UPDATE characters SET ruleset_id = 'd20' "
        "WHERE ruleset_id IS NULL OR ruleset_id NOT IN ('d20')"
    )


def downgrade() -> None:
    # Nothing to undo: 'core' never named a real ruleset.
    pass
