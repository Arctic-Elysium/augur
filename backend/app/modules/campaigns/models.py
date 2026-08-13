from __future__ import annotations

import enum
import uuid

from sqlalchemy import Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base, Timestamped, UUIDPrimaryKey


class PlayMode(str, enum.Enum):
    """The three modes. Everything downstream branches on this, and only this."""

    SOLO = "solo"           # one player, AI GM, immediate turns
    PARTY_AI_GM = "party"   # many players, AI GM, batched rounds
    PARTY_HUMAN_GM = "table"  # many players, human GM, AI proposes only


class CampaignStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class Campaign(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "campaigns"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    premise: Mapped[str | None] = mapped_column(Text, nullable=True)
    ruleset_id: Mapped[str] = mapped_column(String(64), default="d20")
    play_mode: Mapped[PlayMode] = mapped_column(
        Enum(PlayMode, native_enum=False), default=PlayMode.SOLO
    )
    status: Mapped[CampaignStatus] = mapped_column(
        Enum(CampaignStatus, native_enum=False), default=CampaignStatus.DRAFT
    )
    # Tone, content dials, difficulty, house rules. Schema-validated in the
    # service layer, stored loose so settings can evolve without migrations.
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)


class CampaignRole(str, enum.Enum):
    OWNER = "owner"
    GM = "gm"
    PLAYER = "player"
    OBSERVER = "observer"


class CampaignMember(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "campaign_members"
    __table_args__ = (UniqueConstraint("campaign_id", "user_id"),)

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[CampaignRole] = mapped_column(
        Enum(CampaignRole, native_enum=False), default=CampaignRole.PLAYER
    )
