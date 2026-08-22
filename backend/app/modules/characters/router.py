"""Character CRUD.

Solo play means one user owning several sheets, so nothing here assumes a
one-character party. Creation goes through the ruleset so an invalid sheet is
rejected by the system that will have to interpret it.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.auth.deps import DbDep, PrincipalDep
from app.core.errors import ForbiddenError, InvalidRequest, NotFoundError
from app.modules.campaigns.access import Access, resolve_access
from app.modules.campaigns.models import Campaign
from app.modules.characters.models import Character, Controller
from app.modules.identity.service import IdentityService
from app.modules.characters.kit import StartingKit
from app.modules.memory.service import MemoryService, make_ref
from app.modules.rules import registry

router = APIRouter()


class Hook(BaseModel):
    """A concrete thread the GM can pull on.

    Deliberately structured rather than free prose: "a person you owe" with a
    name and a debt is usable by the engine, where three paragraphs of prose is
    only usable by a human who read them.
    """

    kind: str = Field(max_length=40)   # bond | debt | flaw | goal | fear | secret
    subject: str = Field(max_length=120)
    detail: str = Field(max_length=500, default="")


class CharacterCreate(BaseModel):
    campaign_id: uuid.UUID
    name: str = Field(min_length=1, max_length=120)
    attributes: dict[str, int]
    skills: dict[str, int] = Field(default_factory=dict)
    controller: Controller = Controller.PLAYER
    backstory: str | None = Field(default=None, max_length=8000)
    hooks: list[Hook] = Field(default_factory=list, max_length=6)


class CharacterUpdate(BaseModel):
    """Post-creation edits. Attributes are absent on purpose - rewriting them
    after play has started would invalidate every roll already made."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    backstory: str | None = Field(default=None, max_length=8000)
    hooks: list[Hook] | None = Field(default=None, max_length=6)
    inventory: list[str] | None = Field(default=None, max_length=100)


class CharacterOut(BaseModel):
    id: uuid.UUID
    campaign_id: uuid.UUID
    name: str
    controller: Controller
    active: bool
    archived_reason: str | None = None
    epitaph: str | None = None
    sheet: dict
    backstory: str | None = None
    hooks: list[dict] = []

    model_config = {"from_attributes": True}


async def _access(db, principal, campaign_id: uuid.UUID) -> Access:
    user = await IdentityService(db).get_by_subject(principal.subject)
    if user is None:
        raise NotFoundError("campaign not found")
    return await resolve_access(db, user.id, campaign_id, principal=principal)


async def _member_campaign(db, principal, campaign_id: uuid.UUID) -> Campaign:
    return (await _access(db, principal, campaign_id)).campaign


async def _owned(db, principal, character_id: uuid.UUID) -> tuple[Character, Access]:
    """Fetch a character the caller may modify.

    Owner or game master. Without this a player could PATCH anyone's sheet by
    guessing an id - the endpoint checked campaign membership and stopped
    there, which is the same check for every member of the table.
    """
    row = await db.get(Character, character_id)
    if row is None:
        raise NotFoundError("character not found")
    access = await _access(db, principal, row.campaign_id)
    if row.owner_id != access.user_id and not access.runs_the_game:
        raise ForbiddenError("that is not your character")
    return row, access


@router.post("", response_model=CharacterOut, status_code=201)
async def create_character(
    payload: CharacterCreate, request: Request, db: DbDep, principal: PrincipalDep
) -> Character:
    campaign = await _member_campaign(db, principal, payload.campaign_id)
    user = await IdentityService(db).get_by_subject(principal.subject)

    try:
        ruleset = registry.get(campaign.ruleset_id)
    except InvalidRequest as exc:
        # Names the campaign, not just the id. "unknown ruleset: core" arriving
        # from a character POST gives no hint that the *campaign* is the thing
        # that is wrong.
        raise InvalidRequest(
            f"campaign '{campaign.name}' is bound to an unknown ruleset "
            f"'{campaign.ruleset_id}'",
            detail={"hint": "this campaign predates the ruleset registry"},
        ) from exc
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
        backstory=payload.backstory,
        hooks=[h.model_dump() for h in payload.hooks],
        sheet={
            "attributes": built.attributes,
            "skills": built.skills,
            "hp": built.hp,
            "hp_max": built.hp_max,
            "conditions": [],
            "inventory": [],
            "level": built.level,
        },
    )
    db.add(row)
    await db.flush()

    # Threads become canon immediately. Without this the hooks are inert - a
    # player writes "I owe Karal for the boat" and the GM never sees it again.
    await MemoryService(db, campaign.id).seed_from_character(
        payload.name, payload.backstory, [h.model_dump() for h in payload.hooks]
    )

    # Starting kit, inferred from what they wrote about themselves. A player
    # who describes a locksmith should not have to also type "lockpicks" into
    # an inventory box - and an item the fiction already gave them is the kind
    # of thing the game master should be able to reach for.
    if payload.backstory or payload.hooks:
        items = await StartingKit(request.app.state.ai).infer(
            name=payload.name,
            backstory=payload.backstory or "",
            hooks=[h.model_dump() for h in payload.hooks],
        )
        if items:
            row.sheet = {**row.sheet, "inventory": items}
            await db.flush()

    return row


# Fields another player has no business reading. Vitals and conditions stay
# visible - the party can see that someone is bleeding - but what is in
# somebody's pack, what they wrote about themselves, and what they are hiding
# are theirs.
_PRIVATE_SHEET_KEYS = ("inventory",)
_PRIVATE_FIELDS = ("backstory", "hooks")


def _redact(row: Character) -> CharacterOut:
    """A sheet as seen by someone who does not own it.

    Health and conditions survive because the party can see them in the
    fiction; a bleeding companion is not a secret. Inventory, backstory and
    threads do not - reading another player's threads spoils the reveals they
    were written for.
    """
    sheet = {k: v for k, v in row.sheet.items() if k not in _PRIVATE_SHEET_KEYS}
    sheet["inventory"] = []
    return CharacterOut(
        id=row.id, campaign_id=row.campaign_id, name=row.name,
        controller=row.controller, active=row.active,
        archived_reason=row.archived_reason, epitaph=row.epitaph,
        sheet=sheet, backstory=None, hooks=[],
    )


@router.get("", response_model=list[CharacterOut])
async def list_characters(
    campaign_id: uuid.UUID, db: DbDep, principal: PrincipalDep
) -> list[CharacterOut]:
    access = await _access(db, principal, campaign_id)
    result = await db.execute(
        select(Character).where(Character.campaign_id == campaign_id)
        .order_by(Character.created_at)
    )
    rows = list(result.scalars())

    # The game master sees everything - they have to, to run the game.
    if access.runs_the_game:
        return [CharacterOut.model_validate(r, from_attributes=True) for r in rows]

    return [
        CharacterOut.model_validate(r, from_attributes=True)
        if r.owner_id == access.user_id
        else _redact(r)
        for r in rows
    ]


@router.patch("/{character_id}", response_model=CharacterOut)
async def update_character(
    character_id: uuid.UUID,
    payload: CharacterUpdate,
    db: DbDep,
    principal: PrincipalDep,
) -> Character:
    row, _ = await _owned(db, principal, character_id)

    if payload.name is not None:
        row.name = payload.name
    if payload.backstory is not None:
        row.backstory = payload.backstory
    if payload.hooks is not None:
        row.hooks = [h.model_dump() for h in payload.hooks]
    if payload.inventory is not None:
        # Copy-on-write: SQLAlchemy does not see in-place mutation of a JSONB
        # dict, so the change would be silently dropped on commit.
        row.sheet = {**row.sheet, "inventory": payload.inventory}

    await db.flush()
    return row


class ArchiveIn(BaseModel):
    reason: Literal["dead", "retired", "missing"] = "retired"
    epitaph: str | None = Field(default=None, max_length=1000)


@router.post("/{character_id}/archive", response_model=CharacterOut)
async def archive_character(
    character_id: uuid.UUID, payload: ArchiveIn, db: DbDep, principal: PrincipalDep
) -> Character:
    """Leaves the party, stays in the record.

    Never deleted: a dead character is still part of what happened, still owed
    things, still worth avenging - and the game master needs to be able to
    refer to them. Their canon facts stay too.
    """
    row, _ = await _owned(db, principal, character_id)

    row.active = False
    row.archived_reason = payload.reason
    row.epitaph = payload.epitaph

    # Record it, so the game master knows and can speak of it.
    memory = MemoryService(db, row.campaign_id)
    verb = {"dead": "died", "missing": "went missing", "retired": "left the party"}[
        payload.reason
    ]
    await memory.add_fact(
        subject_ref=make_ref("npc", row.name),
        predicate=verb,
        object_text=payload.epitaph or "no further detail recorded",
    )

    await db.flush()
    return row


@router.post("/{character_id}/restore", response_model=CharacterOut)
async def restore_character(
    character_id: uuid.UUID, db: DbDep, principal: PrincipalDep
) -> Character:
    """Undo an archive. Mis-clicking should not cost a character."""
    row, _ = await _owned(db, principal, character_id)
    row.active = True
    row.archived_reason = None
    await db.flush()
    return row
