from __future__ import annotations

import uuid

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.auth.deps import DbDep, PrincipalDep
from app.core.errors import NotFoundError
from app.modules.campaigns.models import (
    Campaign,
    CampaignMember,
    CampaignRole,
    PlayMode,
)
from app.modules.identity.service import IdentityService
from app.modules.rules import registry

router = APIRouter()


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    premise: str | None = None
    play_mode: PlayMode = PlayMode.SOLO
    ruleset_id: str = "d20"


class CampaignOut(BaseModel):
    id: uuid.UUID
    name: str
    premise: str | None
    play_mode: PlayMode
    status: str

    model_config = {"from_attributes": True}


@router.post("", response_model=CampaignOut, status_code=201)
async def create_campaign(
    payload: CampaignCreate, db: DbDep, principal: PrincipalDep
) -> Campaign:
    user = await IdentityService(db).upsert_from_principal(principal)
    registry.get(payload.ruleset_id)
    campaign = Campaign(
        owner_id=user.id,
        name=payload.name,
        premise=payload.premise,
        play_mode=payload.play_mode,
        ruleset_id=payload.ruleset_id,
    )
    db.add(campaign)
    await db.flush()
    db.add(
        CampaignMember(
            campaign_id=campaign.id, user_id=user.id, role=CampaignRole.OWNER
        )
    )
    return campaign


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
