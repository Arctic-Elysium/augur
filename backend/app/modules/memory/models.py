from __future__ import annotations

import enum
import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base, Timestamped, UUIDPrimaryKey


class EntryStatus(str, enum.Enum):
    """Whether a human has accepted this into canon.

    Only two states, deliberately. A "rejected" state would need a tombstone
    and a re-proposal policy; deleting instead means the next time the thing
    genuinely comes up, extraction offers it again - which is exactly what
    "not now" should mean.
    """

    PROPOSED = "proposed"
    ACCEPTED = "accepted"


class EntityKind(str, enum.Enum):
    NPC = "npc"
    LOCATION = "location"
    FACTION = "faction"
    ITEM = "item"
    CREATURE = "creature"
    CONCEPT = "concept"


class Entity(UUIDPrimaryKey, Timestamped, Base):
    """A thing the world contains.

    The `ref` is the stable handle everything else points at - canon facts,
    check locks, event references. It is slug-shaped (`npc:serel`,
    `location:the-study`) rather than a UUID because the model has to be able
    to produce it, and a model can reliably echo a slug it has seen while it
    cannot reliably echo a UUID.
    """

    __tablename__ = "entities"
    __table_args__ = (
        Index("ix_entities_campaign_ref", "campaign_id", "ref", unique=True),
    )

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    ref: Mapped[str] = mapped_column(String(160))
    kind: Mapped[EntityKind] = mapped_column(Enum(EntityKind, native_enum=False))
    name: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text, default="")

    # Where it was first established, for provenance.
    first_seen_session: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Bumped every time the entity comes up. Retrieval prefers the frequently
    # relevant over the merely recent.
    mentions: Mapped[int] = mapped_column(Integer, default=1)

    # Mutable state the model may update: disposition, status, whereabouts.
    state: Mapped[dict] = mapped_column(JSONB, default=dict)

    # False until the party has actually encountered it. The GM may know about
    # an NPC long before the players do, and the Codex must not spoil that.
    known_to_players: Mapped[bool] = mapped_column(Boolean, default=True)

    # Whether the GM has accepted this into canon. Orthogonal to
    # `known_to_players`: that is about the party's knowledge, this is about
    # the record's authority. Extraction writes PROPOSED; only a human writes
    # ACCEPTED. Rejection deletes the row - "not now" means the next mention
    # proposes it again, which is what happens naturally.
    status: Mapped[EntryStatus] = mapped_column(
        Enum(EntryStatus, native_enum=False), default=EntryStatus.PROPOSED
    )
    proposed_in_session: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # What this entry used to say. Appended to when the entry is transformed
    # rather than corrected: Borveld ran an inn, then died, then came back
    # wrong, and the model should know all three in order. A correction edits
    # in place and leaves no history; a transformation preserves what was.
    #
    # Shape: [{"session": 4, "summary": "...", "note": "killed by the party"}]
    history: Mapped[list] = mapped_column(JSONB, default=list)


class CanonFact(UUIDPrimaryKey, Timestamped, Base):
    """A concrete claim the world may not contradict.

    Separate from summaries because summaries compress and lose specifics,
    while a contradiction at session 40 is almost always about a specific:
    a name, a relationship, a thing that happened. Facts do not compress.
    """

    __tablename__ = "canon_facts"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    subject_ref: Mapped[str] = mapped_column(String(160), index=True)
    predicate: Mapped[str] = mapped_column(String(120))
    object_text: Mapped[str] = mapped_column(Text)

    # Which turn established it, so a disputed fact can be traced to its scene.
    source_turn_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("turns.id", ondelete="SET NULL"), nullable=True
    )
    session_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # GM-only facts exist so a prepared adventure can carry its secrets: the
    # model needs to know them to run the scene, the Codex must not show them.
    secret: Mapped[bool] = mapped_column(Boolean, default=False)
    # Set when the fact was never true - a mis-extraction, a mistake. The
    # model must never see it again.
    retracted: Mapped[bool] = mapped_column(Boolean, default=False)

    # Proposed by extraction, accepted by a human. Same contract as Entity.
    status: Mapped[EntryStatus] = mapped_column(
        Enum(EntryStatus, native_enum=False), default=EntryStatus.PROPOSED
    )

    # Supersession is not retraction, and conflating them loses the good part.
    # Retracted: this was never true. Superseded: this WAS true and then
    # stopped being - Borveld really did run that inn before he died, the
    # townspeople remember it, and the model should too. A superseded fact is
    # rendered as history rather than as current canon, and is no longer a
    # contradiction with the fact that replaced it.
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("canon_facts.id", ondelete="SET NULL"), nullable=True
    )
    superseded_at_session: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SummaryLevel(int, enum.Enum):
    SCENE = 0
    SESSION = 1
    CHAPTER = 2


class Summary(UUIDPrimaryKey, Timestamped, Base):
    """A rung on the compression ladder.

    Scenes roll into sessions roll into chapters. This is what keeps context
    bounded: session 40 loads forty one-line session summaries and a handful of
    chapter digests, not forty transcripts.
    """

    __tablename__ = "summaries"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    level: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200), default="")
    body: Mapped[str] = mapped_column(Text)
    session_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Refs of entities that appear, so retrieval can pull the right rung.
    entity_refs: Mapped[list] = mapped_column(JSONB, default=list)


class Note(UUIDPrimaryKey, Timestamped, Base):
    """The player's own writing.

    Deliberately never fed to the model. A journal you suspect the GM is
    reading is a journal you stop being honest in - and half the point of
    taking notes is recording suspicions you are not ready to act on.
    """

    __tablename__ = "notes"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    session_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
