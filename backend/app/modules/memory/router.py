"""Codex and journal endpoints.

The split that matters here: the codex is what the *world* established and the
journal is what the *player* wrote. Secret facts never cross into the codex,
and notes never cross into the model's context.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.auth.deps import DbDep, PrincipalDep
from app.core.errors import ForbiddenError, NotFoundError
from app.modules.campaigns.models import Campaign, CampaignMember
from app.modules.identity.service import IdentityService
from app.modules.memory.models import Note
from app.modules.memory.service import MemoryService

router = APIRouter()


async def _authorize(db, principal, campaign_id: uuid.UUID):
    user = await IdentityService(db).get_by_subject(principal.subject)
    if user is None:
        raise ForbiddenError("no such user")
    result = await db.execute(
        select(Campaign)
        .join(CampaignMember, CampaignMember.campaign_id == Campaign.id)
        .where(Campaign.id == campaign_id, CampaignMember.user_id == user.id)
    )
    campaign = result.scalar_one_or_none()
    if campaign is None:
        raise NotFoundError("campaign not found")
    return campaign, user


class EntityOut(BaseModel):
    ref: str
    kind: str
    name: str
    summary: str
    mentions: int
    first_seen_session: int | None
    state: dict


class FactOut(BaseModel):
    subject_ref: str
    predicate: str
    object_text: str
    session_number: int | None


class NoteIn(BaseModel):
    title: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=20000)
    pinned: bool = False
    session_number: int | None = None


class NoteOut(BaseModel):
    id: uuid.UUID
    title: str
    body: str
    pinned: bool
    session_number: int | None

    model_config = {"from_attributes": True}


@router.get("/codex")
async def codex(
    campaign_id: uuid.UUID, db: DbDep, principal: PrincipalDep
) -> dict:
    """What the party has established.

    `known_only` and the secret filter are both on: a prepared adventure's
    secrets live in canon so the GM can run the scene, and showing them here
    would spoil the thing the player is trying to find out.
    """
    await _authorize(db, principal, campaign_id)
    memory = MemoryService(db, campaign_id)

    entities = await memory.entities(known_only=True)
    facts = await memory.facts(include_secret=False)

    by_subject: dict[str, list[FactOut]] = {}
    for fact in facts:
        by_subject.setdefault(fact.subject_ref, []).append(
            FactOut(
                subject_ref=fact.subject_ref,
                predicate=fact.predicate,
                object_text=fact.object_text,
                session_number=fact.session_number,
            )
        )

    return {
        "entities": [
            {
                **EntityOut(
                    ref=e.ref, kind=e.kind.value, name=e.name, summary=e.summary,
                    mentions=e.mentions, first_seen_session=e.first_seen_session,
                    state=e.state,
                ).model_dump(),
                "facts": [f.model_dump() for f in by_subject.get(e.ref, [])],
            }
            for e in entities
        ],
        "unattached_facts": [
            f.model_dump()
            for ref, group in by_subject.items()
            if not any(e.ref == ref for e in entities)
            for f in group
        ],
    }


@router.get("/notes", response_model=list[NoteOut])
async def list_notes(
    campaign_id: uuid.UUID, db: DbDep, principal: PrincipalDep
) -> list[Note]:
    _, user = await _authorize(db, principal, campaign_id)
    return await MemoryService(db, campaign_id).notes(user.id)


@router.post("/notes", response_model=NoteOut, status_code=201)
async def create_note(
    campaign_id: uuid.UUID, payload: NoteIn, db: DbDep, principal: PrincipalDep
) -> Note:
    _, user = await _authorize(db, principal, campaign_id)
    note = Note(
        campaign_id=campaign_id, author_id=user.id,
        title=payload.title, body=payload.body,
        pinned=payload.pinned, session_number=payload.session_number,
    )
    db.add(note)
    await db.flush()
    return note


@router.patch("/notes/{note_id}", response_model=NoteOut)
async def update_note(
    note_id: uuid.UUID, payload: NoteIn, db: DbDep, principal: PrincipalDep
) -> Note:
    note = await db.get(Note, note_id)
    if note is None:
        raise NotFoundError("note not found")
    _, user = await _authorize(db, principal, note.campaign_id)
    if note.author_id != user.id:
        raise ForbiddenError("not your note")
    note.title = payload.title
    note.body = payload.body
    note.pinned = payload.pinned
    await db.flush()
    return note


@router.delete("/notes/{note_id}", status_code=204)
async def delete_note(
    note_id: uuid.UUID, db: DbDep, principal: PrincipalDep
) -> None:
    note = await db.get(Note, note_id)
    if note is None:
        return
    _, user = await _authorize(db, principal, note.campaign_id)
    if note.author_id != user.id:
        raise ForbiddenError("not your note")
    await db.delete(note)
