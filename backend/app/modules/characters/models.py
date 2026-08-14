from __future__ import annotations

import enum
import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base, Timestamped, UUIDPrimaryKey


class Controller(str, enum.Enum):
    """Who decides this character's actions.

    Solo play defaults every character to PLAYER - you run the whole party, the
    AI runs the world. AI exists now rather than later because adding it after
    characters are persisted means a migration, and because AI-run companions
    are the obvious next thing someone asks for.
    """

    PLAYER = "player"
    AI = "ai"


class Character(UUIDPrimaryKey, Timestamped, Base):
    """A persisted sheet.

    The sheet itself lives in JSONB rather than columns because its shape is
    the ruleset's business, not the database's - a d6-pool system has no
    `hp_max`. The rules engine owns interpretation; this table owns identity,
    ownership, and durability.
    """

    __tablename__ = "characters"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    # Who at the table controls this sheet. In solo play this is the one human
    # for every character in the party.
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    ruleset_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(120))
    controller: Mapped[Controller] = mapped_column(
        Enum(Controller, native_enum=False), default=Controller.PLAYER
    )
    # Retired characters stay for the record but leave the party.
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Why they left. A dead character is not the same as one set aside, and the
    # difference matters to the game master: the dead can be spoken of, owed,
    # and avenged, while someone retired is simply elsewhere.
    archived_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)
    epitaph: Mapped[str | None] = mapped_column(Text, nullable=True)
    sheet: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Free prose. What the player wrote about who this person is.
    backstory: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Structured hooks: a person owed, a place fled, a thing wanted. These are
    # not flavour - they are seeded into canon so the GM can actually use them,
    # which is the difference between a backstory that matters and one that
    # sits unread on a sheet.
    hooks: Mapped[list] = mapped_column(JSONB, default=list)
