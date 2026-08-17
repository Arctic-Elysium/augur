"""Invitations and access.

Roles were always in the schema but nothing enforced them - every member could
see and edit everything. That is fine for a solo campaign and wrong the moment
a second person joins: a player should not be reading another player's
inventory, and only the game master should see what the world is hiding.

Access is decided in one place, `resolve_access`, so a new endpoint cannot
accidentally skip the check by forgetting to write it.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base, Timestamped, UUIDPrimaryKey
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.modules.campaigns.models import Campaign, CampaignMember, CampaignRole

# Short enough to read down a voice call, long enough not to be guessable at
# the rate a rate-limited endpoint allows.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no I/O/0/1
CODE_LENGTH = 8
DEFAULT_TTL_DAYS = 14


class Invite(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "invites"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    # The role the redeemer gets. An invite that granted GM by default would be
    # a footgun; the inviter chooses deliberately.
    role: Mapped[str] = mapped_column(String(20), default=CampaignRole.PLAYER.value)
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Multi-use by default so one code can seat a whole table.
    max_uses: Mapped[int] = mapped_column(Integer, default=8)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)

    @property
    def spent(self) -> bool:
        expiry = self.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return (
            self.revoked
            or self.uses >= self.max_uses
            or expiry < datetime.now(timezone.utc)
        )


@dataclass(frozen=True)
class Access:
    """What this user may do in this campaign.

    Computed once per request and passed down, rather than re-derived in every
    handler - the second derivation is where the rules drift apart.
    """

    campaign: Campaign
    user_id: uuid.UUID
    role: CampaignRole

    @property
    def is_owner(self) -> bool:
        return self.role is CampaignRole.OWNER

    @property
    def runs_the_game(self) -> bool:
        """Owner or GM. Sees what the world is hiding, and everyone's sheets."""
        return self.role in (CampaignRole.OWNER, CampaignRole.GM)

    @property
    def can_play(self) -> bool:
        return self.role in (
            CampaignRole.OWNER,
            CampaignRole.GM,
            CampaignRole.PLAYER,
        )

    def require_gm(self) -> None:
        if not self.runs_the_game:
            raise ForbiddenError("only the game master can do that")

    def require_owner(self) -> None:
        if not self.is_owner:
            raise ForbiddenError("only the campaign owner can do that")

    def require_play(self) -> None:
        if not self.can_play:
            raise ForbiddenError("observers cannot act")


async def resolve_access(
    db: AsyncSession, user_id: uuid.UUID, campaign_id: uuid.UUID
) -> Access:
    """The single gate. Every campaign-scoped endpoint goes through this."""
    result = await db.execute(
        select(Campaign, CampaignMember)
        .join(CampaignMember, CampaignMember.campaign_id == Campaign.id)
        .where(Campaign.id == campaign_id, CampaignMember.user_id == user_id)
    )
    row = result.first()
    if row is None:
        # Deliberately the same error as a missing campaign: telling a stranger
        # that a campaign exists but is not theirs leaks its existence.
        raise NotFoundError("campaign not found")
    campaign, member = row
    return Access(campaign=campaign, user_id=user_id, role=member.role)


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


async def create_invite(
    db: AsyncSession,
    access: Access,
    *,
    role: CampaignRole = CampaignRole.PLAYER,
    max_uses: int = 8,
    ttl_days: int = DEFAULT_TTL_DAYS,
) -> Invite:
    access.require_gm()
    if role is CampaignRole.OWNER:
        raise ConflictError("a campaign has one owner; transfer it instead")

    invite = Invite(
        campaign_id=access.campaign.id,
        code=new_code(),
        role=role.value,
        created_by=access.user_id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=ttl_days),
        max_uses=max(1, min(max_uses, 32)),
    )
    db.add(invite)
    await db.flush()
    return invite


async def redeem_invite(
    db: AsyncSession, user_id: uuid.UUID, code: str
) -> tuple[Campaign, CampaignRole]:
    result = await db.execute(select(Invite).where(Invite.code == code.upper().strip()))
    invite = result.scalar_one_or_none()
    if invite is None or invite.spent:
        # One message for every failure mode. Distinguishing "expired" from
        # "no such code" tells someone guessing codes when they got one right.
        raise NotFoundError("that invite is not valid")

    existing = await db.execute(
        select(CampaignMember).where(
            CampaignMember.campaign_id == invite.campaign_id,
            CampaignMember.user_id == user_id,
        )
    )
    member = existing.scalar_one_or_none()
    campaign = await db.get(Campaign, invite.campaign_id)
    if campaign is None:
        raise NotFoundError("that invite is not valid")

    if member is not None:
        # Already in. Not an error - rejoining from a stale link should land
        # you in the campaign, not on an error page.
        return campaign, member.role

    role = CampaignRole(invite.role)
    db.add(
        CampaignMember(campaign_id=campaign.id, user_id=user_id, role=role)
    )
    invite.uses += 1
    await db.flush()
    return campaign, role
