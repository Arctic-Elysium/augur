"""Character CRUD.

Solo play means one user owning several sheets, so nothing here assumes a
one-character party. Creation goes through the ruleset so an invalid sheet is
rejected by the system that will have to interpret it.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.auth.deps import DbDep, PrincipalDep
from app.core.errors import NotFoundError
from app.modules.campaigns.models import Campaign, CampaignMember
from app.modules.characters.models import Character, Controller
from app.modules.identity.service import IdentityService
from app.modules.rules import registry

router = APIRouter()


class CharacterCreate(BaseModel):
    campaign_id: uuid.UUID
    name: str = Field(min_length=1, max_length=120)
    attributes: dict[str, int]
    skills: dict[str, int] = Field(default_factory=dict)
    controller: Controller = Controller.PLAYER


class CharacterOut(BaseModel):
    id: uuid.UUID
    campaign_id: uuid.UUID
    name: str
    controller: Controller
    active: bool
    sheet: dict

    model_config = {"from_attributes": True}


async def _member_campaign(db, principal, campaign_id: uuid.UUID) -> Campaign:
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
    return campaign


@router.post("", response_model=CharacterOut, status_code=201)
async def create_character(
    payload: CharacterCreate, db: DbDep, principal: PrincipalDep
) -> Character:
    campaign = await _member_campaign(db, principal, payload.campaign_id)
    user = await IdentityService(db).get_by_subject(principal.subject)

    ruleset = registry.get(campaign.ruleset_id)
    # Validate through the ruleset - it owns what a legal sheet looks like.
    built = ruleset.create_character({
        "id": str(uuid.uuid4()),
        "name": payload.name,
        "attributes": payload.attributes,
        "skills": payload.skills,
    })

    row = Character(
        campaign_id=campaign.id,
        owner_id=user.id,
        ruleset_id=campaign.ruleset_id,
        name=payload.name,
        controller=payload.controller,
        sheet={
            "attributes": built.attributes,
            "skills": built.skills,
            "hp": built.hp,
            "hp_max": built.hp_max,
            "stress": built.stress,
            "stress_max": built.stress_max,
            "conditions": [],
            "inventory": [],
            "level": built.level,
        },
    )
    db.add(row)
    await db.flush()
    return row


@router.get("", response_model=list[CharacterOut])
async def list_characters(
    campaign_id: uuid.UUID, db: DbDep, principal: PrincipalDep
) -> list[Character]:
    await _member_campaign(db, principal, campaign_id)
    result = await db.execute(
        select(Character).where(Character.campaign_id == campaign_id)
        .order_by(Character.created_at)
    )
    return list(result.scalars())


@router.post("/{character_id}/retire", response_model=CharacterOut)
async def retire_character(
    character_id: uuid.UUID, db: DbDep, principal: PrincipalDep
) -> Character:
    """Retired characters leave the party but stay for the record."""
    row = await db.get(Character, character_id)
    if row is None:
        raise NotFoundError("character not found")
    await _member_campaign(db, principal, row.campaign_id)
    row.active = False
    await db.flush()
    return row
