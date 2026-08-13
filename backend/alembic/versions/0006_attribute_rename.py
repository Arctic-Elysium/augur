"""rename attributes to the familiar six

Revision ID: 0006
Revises: 0005

Sheets live in JSONB, so this rewrites keys in place. Data migration rather
than a schema change: an existing character keeps its scores, they just answer
to standard names now.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RENAME = {
    "might": "strength",
    "agility": "dexterity",
    "endurance": "constitution",
    "wits": "intelligence",
    "insight": "wisdom",
    "presence": "charisma",
}


def _rewrite(mapping: dict[str, str]) -> None:
    conn = op.get_bind()
    rows = conn.exec_driver_sql(
        "SELECT id, sheet FROM characters WHERE sheet ? 'attributes'"
    ).fetchall()
    for row_id, sheet in rows:
        attrs = sheet.get("attributes") or {}
        renamed = {mapping.get(k, k): v for k, v in attrs.items()}
        if renamed == attrs:
            continue
        conn.exec_driver_sql(
            "UPDATE characters SET sheet = jsonb_set(sheet::jsonb, '{attributes}', %s::jsonb) "
            "WHERE id = %s",
            (__import__("json").dumps(renamed), row_id),
        )


def upgrade() -> None:
    _rewrite(_RENAME)


def downgrade() -> None:
    _rewrite({v: k for k, v in _RENAME.items()})
