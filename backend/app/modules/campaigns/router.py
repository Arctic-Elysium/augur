from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.auth.deps import AdminDep, DbDep, PrincipalDep
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.modules.campaigns.access import (
    Access,
    Invite,
    create_invite,
    redeem_invite,
    resolve_access,
)
from app.modules.campaigns.models import (
    Campaign,
    CampaignMember,
    CampaignRole,
    CampaignStatus,
)
from app.modules.identity.models import User
from app.modules.identity.models import User
from app.modules.identity.service import IdentityService
from app.modules.sessions.models import PlaySession, Turn
from app.modules.rules import registry

router = APIRouter()


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    premise: str | None = None
    # 'core' was the Milestone 0 placeholder this field used to default to.
    # Migration 0007 repaired the rows it produced - and then every new
    # campaign recreated the bug, because the repair fixed the data and not
    # the default. The default is now the real system, and creation validates
    # against the registry so an unknown ruleset dies here as a 422 instead of
    # surfacing later as a character-creation failure three screens away.
    ruleset_id: str = "d20"
    # Setting notes for a prepared campaign - a starter set summarised in the
    # owner's own words, house lore, an ongoing world. Pinned into every
    # turn's context, capped by the builder's primer budget.
    primer: str | None = Field(default=None, max_length=60_000)
    tone: str | None = Field(default=None, max_length=400)


class CampaignOut(BaseModel):
    id: uuid.UUID
    name: str
    premise: str | None
    status: str

    model_config = {"from_attributes": True}


@router.post("", response_model=CampaignOut, status_code=201)
async def create_campaign(
    payload: CampaignCreate, db: DbDep, principal: PrincipalDep
) -> Campaign:
    registry.get(payload.ruleset_id)  # raises InvalidRequest for an unknown id
    user = await IdentityService(db).upsert_from_principal(principal)
    settings: dict = {}
    if payload.primer and payload.primer.strip():
        settings["primer"] = payload.primer.strip()
    if payload.tone and payload.tone.strip():
        settings["tone"] = payload.tone.strip()
    campaign = Campaign(
        owner_id=user.id,
        name=payload.name,
        premise=payload.premise,
        ruleset_id=payload.ruleset_id,
        settings=settings,
    )
    db.add(campaign)
    await db.flush()
    db.add(
        CampaignMember(
            campaign_id=campaign.id, user_id=user.id, role=CampaignRole.OWNER
        )
    )
    return campaign


class SettingsIn(BaseModel):
    """Everything that steers the model, in one place.

    Stored in `settings` JSONB rather than as columns so these can evolve
    without a migration each time - which matters here because directives in
    particular are a list nobody can size in advance.
    """

    primer: str | None = Field(default=None, max_length=60_000)
    tone: str | None = Field(default=None, max_length=400)
    # The long arc. Sessions hang off it; nothing should foreclose it.
    arc: str | None = Field(default=None, max_length=4_000)
    # Standing corrections. "Borveld is dead - never voice him." The fix for
    # drift that recurs, as opposed to drift you catch once and amend.
    directives: list[str] | None = Field(default=None, max_length=40)
    debug_prompts: bool | None = None
    # When false, the engine refuses items a player named that canon has never
    # heard of. Default true: with a human approving durable writes, this is
    # taste, not safety, and taste belongs to the person at the table.
    allow_player_grants: bool | None = None


@router.patch("/{campaign_id}/settings")
async def update_settings(
    campaign_id: uuid.UUID, payload: SettingsIn, db: DbDep, principal: PrincipalDep
) -> dict:
    _, user = await _campaign_and_user(db, principal, campaign_id)
    (await resolve_access(db, user.id, campaign_id, principal=principal)).require_gm()
    campaign = await db.get(Campaign, campaign_id)
    if campaign is None:
        raise NotFoundError("campaign not found")

    settings = dict(campaign.settings or {})
    for key in ("primer", "tone", "arc"):
        value = getattr(payload, key)
        if value is not None:
            cleaned = value.strip()
            if cleaned:
                settings[key] = cleaned
            else:
                settings.pop(key, None)
    if payload.directives is not None:
        settings["directives"] = [
            d.strip() for d in payload.directives if d.strip()
        ][:40]
    if payload.debug_prompts is not None:
        settings["debug_prompts"] = payload.debug_prompts
    if payload.allow_player_grants is not None:
        settings["allow_player_grants"] = payload.allow_player_grants

    # Reassigned rather than mutated: SQLAlchemy does not track in-place edits
    # to a JSONB dict, and a mutation here would silently never persist.
    campaign.settings = settings
    await db.flush()
    return settings


@router.get("/{campaign_id}/settings")
async def get_settings(
    campaign_id: uuid.UUID, db: DbDep, principal: PrincipalDep
) -> dict:
    campaign, user = await _campaign_and_user(db, principal, campaign_id)
    access = await resolve_access(db, user.id, campaign_id, principal=principal)
    settings = dict(campaign.settings or {})
    if not access.runs_the_game:
        # The primer, the arc and the destination are the GM's prep. Handing
        # them to a player is handing them the answers.
        for key in ("primer", "arc", "directives", "debug_prompts"):
            settings.pop(key, None)
    return settings


async def _campaign_and_user(db, principal, campaign_id: uuid.UUID):
    user = await IdentityService(db).get_by_subject(principal.subject)
    if user is None:
        raise NotFoundError("campaign not found")
    result = await db.execute(
        select(Campaign)
        .join(CampaignMember, CampaignMember.campaign_id == Campaign.id)
        .where(Campaign.id == campaign_id, CampaignMember.user_id == user.id)
    )
    campaign = result.scalar_one_or_none()
    if campaign is None:
        raise NotFoundError("campaign not found")
    return campaign, user


@router.get("", response_model=list[CampaignOut])
async def list_campaigns(db: DbDep, principal: PrincipalDep) -> list[Campaign]:
    user = await IdentityService(db).get_by_subject(principal.subject)
    if user is None:
        return []
    result = await db.execute(
        select(Campaign)
        .join(CampaignMember, CampaignMember.campaign_id == Campaign.id)
        .where(CampaignMember.user_id == user.id)
        .order_by(Campaign.created_at.desc())
    )
    return list(result.scalars().all())


class InviteCreate(BaseModel):
    role: CampaignRole = CampaignRole.PLAYER
    max_uses: int = Field(default=8, ge=1, le=32)
    ttl_days: int = Field(default=14, ge=1, le=90)


class InviteOut(BaseModel):
    code: str
    role: str
    uses: int
    max_uses: int
    expires_at: datetime
    spent: bool


class MemberOut(BaseModel):
    user_id: uuid.UUID
    role: CampaignRole
    display_name: str | None
    email: str | None
    is_you: bool


class RoleChange(BaseModel):
    role: CampaignRole


async def _access(db, principal, campaign_id: uuid.UUID) -> Access:
    user = await IdentityService(db).get_by_subject(principal.subject)
    if user is None:
        raise NotFoundError("campaign not found")
    return await resolve_access(db, user.id, campaign_id, principal=principal)


@router.get("/{campaign_id}/members", response_model=list[MemberOut])
async def list_members(
    campaign_id: uuid.UUID, db: DbDep, principal: PrincipalDep
) -> list[MemberOut]:
    access = await _access(db, principal, campaign_id)
    result = await db.execute(
        select(CampaignMember, User)
        .join(User, User.id == CampaignMember.user_id)
        .where(CampaignMember.campaign_id == campaign_id)
        .order_by(CampaignMember.created_at)
    )
    return [
        MemberOut(
            user_id=m.user_id,
            role=m.role,
            display_name=u.display_name,
            # Only the game master sees addresses. A player list should not
            # hand every player everyone else's email.
            email=u.email if access.runs_the_game else None,
            is_you=m.user_id == access.user_id,
        )
        for m, u in result.all()
    ]


@router.post("/{campaign_id}/invites", response_model=InviteOut, status_code=201)
async def create_campaign_invite(
    campaign_id: uuid.UUID, payload: InviteCreate, db: DbDep, principal: PrincipalDep
) -> InviteOut:
    access = await _access(db, principal, campaign_id)
    invite = await create_invite(
        db, access, role=payload.role,
        max_uses=payload.max_uses, ttl_days=payload.ttl_days,
    )
    return InviteOut(
        code=invite.code, role=invite.role, uses=invite.uses,
        max_uses=invite.max_uses, expires_at=invite.expires_at, spent=False,
    )


@router.get("/{campaign_id}/invites", response_model=list[InviteOut])
async def list_campaign_invites(
    campaign_id: uuid.UUID, db: DbDep, principal: PrincipalDep
) -> list[InviteOut]:
    access = await _access(db, principal, campaign_id)
    access.require_gm()
    result = await db.execute(
        select(Invite).where(Invite.campaign_id == campaign_id)
        .order_by(Invite.created_at.desc())
    )
    return [
        InviteOut(
            code=i.code, role=i.role, uses=i.uses, max_uses=i.max_uses,
            expires_at=i.expires_at, spent=i.spent,
        )
        for i in result.scalars()
    ]


@router.delete("/{campaign_id}/invites/{code}", status_code=204)
async def revoke_invite(
    campaign_id: uuid.UUID, code: str, db: DbDep, principal: PrincipalDep
) -> None:
    access = await _access(db, principal, campaign_id)
    access.require_gm()
    result = await db.execute(
        select(Invite).where(
            Invite.campaign_id == campaign_id, Invite.code == code.upper()
        )
    )
    invite = result.scalar_one_or_none()
    if invite is not None:
        invite.revoked = True


@router.post("/join", response_model=CampaignOut)
async def join_campaign(
    payload: dict, db: DbDep, principal: PrincipalDep
) -> Campaign:
    """Redeem a code. Not campaign-scoped - the joiner is not a member yet."""
    user = await IdentityService(db).upsert_from_principal(principal)
    campaign, _ = await redeem_invite(db, user.id, str(payload.get("code", "")))
    return campaign


@router.patch("/{campaign_id}/members/{user_id}", response_model=MemberOut)
async def change_member_role(
    campaign_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: RoleChange,
    db: DbDep,
    principal: PrincipalDep,
) -> MemberOut:
    access = await _access(db, principal, campaign_id)
    access.require_owner()
    if user_id == access.user_id:
        raise ConflictError("you cannot change your own role")
    if payload.role is CampaignRole.OWNER:
        raise ConflictError("a campaign has one owner; transfer it instead")

    result = await db.execute(
        select(CampaignMember, User)
        .join(User, User.id == CampaignMember.user_id)
        .where(
            CampaignMember.campaign_id == campaign_id,
            CampaignMember.user_id == user_id,
        )
    )
    row = result.first()
    if row is None:
        raise NotFoundError("member not found")
    member, user = row
    member.role = payload.role
    await db.flush()
    return MemberOut(
        user_id=user_id, role=member.role, display_name=user.display_name,
        email=user.email, is_you=False,
    )


@router.delete("/{campaign_id}/members/{user_id}", status_code=204)
async def remove_member(
    campaign_id: uuid.UUID, user_id: uuid.UUID, db: DbDep, principal: PrincipalDep
) -> None:
    """Removes someone from the table. Their characters stay in the record."""
    access = await _access(db, principal, campaign_id)
    if user_id != access.user_id:
        access.require_owner()
    if user_id == access.campaign.owner_id:
        raise ConflictError("the owner cannot be removed")

    result = await db.execute(
        select(CampaignMember).where(
            CampaignMember.campaign_id == campaign_id,
            CampaignMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is not None:
        await db.delete(member)


@router.delete("/{campaign_id}", status_code=204)
async def delete_campaign(
    campaign_id: uuid.UUID, db: DbDep, principal: PrincipalDep
) -> None:
    """Removes a campaign and everything under it.

    Owner only - a player who was invited should not be able to delete the
    table. Cascades to characters, sessions, turns, entities, canon and notes,
    which is why the client confirms with the campaign's name typed out rather
    than a plain yes.
    """
    user = await IdentityService(db).get_by_subject(principal.subject)
    if user is None:
        raise NotFoundError("campaign not found")

    campaign = await db.get(Campaign, campaign_id)
    if campaign is None:
        raise NotFoundError("campaign not found")
    if campaign.owner_id != user.id:
        raise ForbiddenError("only the owner can delete a campaign")

    await db.delete(campaign)


@router.get("/{campaign_id}", response_model=CampaignOut)
async def get_campaign(
    campaign_id: uuid.UUID, db: DbDep, principal: PrincipalDep
) -> Campaign:
    user = await IdentityService(db).get_by_subject(principal.subject)
    result = await db.execute(
        select(Campaign)
        .join(CampaignMember, CampaignMember.campaign_id == Campaign.id)
        .where(Campaign.id == campaign_id, CampaignMember.user_id == user.id)
    )
    campaign = result.scalar_one_or_none()
    if campaign is None:
        raise NotFoundError("campaign not found")
    return campaign


# ------------------------------------------------------------------- admin
#
# Deliberately a separate surface from `GET /campaigns`. Merging every
# campaign on the deployment into the normal list would mean the page you use
# to delete your own campaigns is also the page showing other people's, and
# the two should never be one keystroke apart.


class AdminCampaignOut(BaseModel):
    id: uuid.UUID
    name: str
    premise: str | None
    ruleset_id: str
    status: str
    owner_subject: str
    owner_display_name: str | None
    members: int
    sessions: int
    turns: int
    last_played_at: datetime | None
    created_at: datetime | None
    # Whether the admin viewing this is also a member. Lets the UI mark the
    # difference between "yours" and "someone else's" at a glance.
    is_member: bool


@router.get("/admin/all", response_model=list[AdminCampaignOut])
async def admin_list_campaigns(
    db: DbDep, principal: AdminDep, include_archived: bool = False
) -> list[AdminCampaignOut]:
    """Every campaign on the deployment.

    Guarded by AdminDep, which re-reads the group claim from the verified
    token on every request - so removing someone from the admin group in
    Voidauth revokes this immediately rather than at their next login.
    """
    me = await IdentityService(db).get_by_subject(principal.subject)

    query = (
        select(
            Campaign,
            User.subject,
            User.display_name,
            func.count(func.distinct(CampaignMember.id)).label("members"),
        )
        .join(User, User.id == Campaign.owner_id)
        .outerjoin(CampaignMember, CampaignMember.campaign_id == Campaign.id)
        .group_by(Campaign.id, User.subject, User.display_name)
        .order_by(Campaign.created_at.desc())
    )
    if not include_archived:
        query = query.where(Campaign.status != CampaignStatus.ARCHIVED)
    rows = (await db.execute(query)).all()

    # Play volume per campaign, which is what actually distinguishes a real
    # campaign from the seven test ones - a name never does.
    counts = {
        row.campaign_id: row
        for row in (
            await db.execute(
                select(
                    PlaySession.campaign_id,
                    func.count(func.distinct(PlaySession.id)).label("sessions"),
                    func.count(Turn.id).label("turns"),
                    func.max(Turn.created_at).label("last_played_at"),
                )
                .outerjoin(Turn, Turn.session_id == PlaySession.id)
                .group_by(PlaySession.campaign_id)
            )
        ).all()
    }

    mine = set()
    if me is not None:
        mine = {
            row[0]
            for row in (
                await db.execute(
                    select(CampaignMember.campaign_id).where(
                        CampaignMember.user_id == me.id
                    )
                )
            ).all()
        }

    out: list[AdminCampaignOut] = []
    for campaign, subject, display_name, members in rows:
        play = counts.get(campaign.id)
        out.append(
            AdminCampaignOut(
                id=campaign.id,
                name=campaign.name,
                premise=campaign.premise,
                ruleset_id=campaign.ruleset_id,
                status=(
                    campaign.status.value
                    if hasattr(campaign.status, "value")
                    else str(campaign.status)
                ),
                owner_subject=subject,
                owner_display_name=display_name,
                members=members or 0,
                sessions=getattr(play, "sessions", 0) or 0,
                turns=getattr(play, "turns", 0) or 0,
                last_played_at=getattr(play, "last_played_at", None),
                created_at=campaign.created_at,
                is_member=campaign.id in mine,
            )
        )
    return out
