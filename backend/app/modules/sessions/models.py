from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base, Timestamped, UUIDPrimaryKey


class SessionStatus(str, enum.Enum):
    ACTIVE = "active"
    ENDED = "ended"


class PlaySession(UUIDPrimaryKey, Timestamped, Base):
    """One sitting. A campaign is a sequence of these.

    `seed` makes every roll in the session reproducible, which matters when a
    player disputes an outcome weeks later - you can replay the exact sequence
    rather than take anyone's word for it.
    """

    __tablename__ = "play_sessions"
    __table_args__ = (UniqueConstraint("campaign_id", "number"),)

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    number: Mapped[int] = mapped_column(Integer)
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus, native_enum=False), default=SessionStatus.ACTIVE
    )
    scene_id: Mapped[str] = mapped_column(String(120), default="opening")
    seed: Mapped[int] = mapped_column(Integer)
    # Whose sheet is in the spotlight. Null means the party is acting together.
    active_character_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("characters.id", ondelete="SET NULL"), nullable=True
    )
    # Player-given name. Sessions are otherwise only distinguishable by number,
    # which is useless for finding the one where the bridge collapsed.
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Written at session end; becomes a rung on the summary ladder.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Turn(UUIDPrimaryKey, Timestamped, Base):
    """One exchange: what the player did, what the world did back.

    `tool_calls` and `deltas` are stored alongside the prose so a turn can be
    audited - you can see exactly which checks fired and what state changed,
    rather than inferring it from narration that may have drifted.
    """

    __tablename__ = "turns"
    __table_args__ = (UniqueConstraint("session_id", "ordinal"),)

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("play_sessions.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    # Null when the input was addressed to the party rather than one character.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("characters.id", ondelete="SET NULL"), nullable=True
    )
    player_input: Mapped[str] = mapped_column(Text)
    narration: Mapped[str] = mapped_column(Text, default="")
    tool_calls: Mapped[list] = mapped_column(JSONB, default=list)
    deltas: Mapped[list] = mapped_column(JSONB, default=list)
    scene_id: Mapped[str] = mapped_column(String(120), default="opening")
    prompt_version: Mapped[str] = mapped_column(String(32), default="")


class SessionClock(UUIDPrimaryKey, Timestamped, Base):
    """Clocks outlive the session that created them - a faction's plan does not
    reset because you stopped playing for the night."""

    __tablename__ = "session_clocks"
    __table_args__ = (UniqueConstraint("campaign_id", "clock_id"),)

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    clock_id: Mapped[str] = mapped_column(String(120))
    label: Mapped[str] = mapped_column(String(300))
    size: Mapped[int] = mapped_column(Integer)
    filled: Mapped[int] = mapped_column(Integer, default=0)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)


class CheckLock(UUIDPrimaryKey, Timestamped, Base):
    """Persisted check ledger.

    In memory this would reset on pod restart, which would quietly hand the
    player a fresh attempt at everything they already failed - exactly the
    exploit locking exists to close.
    """

    __tablename__ = "check_locks"
    __table_args__ = (
        UniqueConstraint("campaign_id", "actor_ref", "kind_id", "target_ref", "fingerprint"),
    )

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    actor_ref: Mapped[str] = mapped_column(String(64))
    kind_id: Mapped[str] = mapped_column(String(64))
    target_ref: Mapped[str] = mapped_column(String(300))
    fingerprint: Mapped[str] = mapped_column(String(64))
    scene_id: Mapped[str] = mapped_column(String(120))
    result: Mapped[dict] = mapped_column(JSONB)
