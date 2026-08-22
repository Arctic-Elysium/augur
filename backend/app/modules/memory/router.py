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

from app.core.auth.deps import DbDep, PrincipalDep, is_platform_admin
from app.core.config.settings import get_settings
from app.core.errors import ForbiddenError, InvalidRequest, NotFoundError
from app.modules.campaigns.models import Campaign, CampaignMember
from app.modules.identity.service import IdentityService
from app.modules.campaigns.access import resolve_access
from app.modules.memory.models import CanonFact, EntityKind, EntryStatus, Note
from app.modules.memory.service import MemoryService, make_ref

router = APIRouter()


async def _authorize(db, principal, campaign_id: uuid.UUID):
    return await campaign_for(db, principal, campaign_id)

async def campaign_for(db, principal, campaign_id: uuid.UUID):
    """Resolve a campaign for this principal, or 404.

    Membership is the normal path. A platform administrator resolves any
    campaign, which is the whole point of the admin view - being able to open
    a test campaign somebody else owns without joining it and polluting its
    member list.

    The 404 for everyone else is deliberate and unchanged: telling a stranger
    that a campaign exists but is not theirs leaks its existence.
    """
    user = await IdentityService(db).get_by_subject(principal.subject)
    if user is None:
        raise ForbiddenError("no such user")
    result = await db.execute(
        select(Campaign)
        .join(CampaignMember, CampaignMember.campaign_id == Campaign.id)
        .where(Campaign.id == campaign_id, CampaignMember.user_id == user.id)
    )
    campaign = result.scalar_one_or_none()
    if campaign is None and is_platform_admin(principal, get_settings()):
        campaign = await db.get(Campaign, campaign_id)
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
    status: str = "accepted"
    known_to_players: bool = True
    history: list = Field(default_factory=list)


class FactOut(BaseModel):
    id: uuid.UUID
    subject_ref: str
    predicate: str
    object_text: str
    session_number: int | None
    secret: bool = False


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
    campaign, user = await _authorize(db, principal, campaign_id)
    access = await resolve_access(db, user.id, campaign_id, principal=principal)
    memory = MemoryService(db, campaign_id)

    # The game master sees what the world is hiding; the table does not. This
    # is the mechanism a prepared adventure uses to keep its secrets while the
    # GM still knows them.
    entities = await memory.entities(known_only=not access.runs_the_game)
    facts = await memory.facts(include_secret=access.runs_the_game)
    # Superseded facts are shown to the GM as history. Players see only what
    # is currently true - "Borveld ran the Bent Axle (until session 4)" in a
    # player-facing codex is a spoiler about a death they may not know about.
    history = (
        [
            f for f in await memory.facts(
                include_secret=True, include_superseded=True
            )
            if f.superseded_by_id is not None
        ]
        if access.runs_the_game else []
    )

    by_subject: dict[str, list[FactOut]] = {}
    for fact in facts:
        by_subject.setdefault(fact.subject_ref, []).append(
            FactOut(
                id=fact.id,
                subject_ref=fact.subject_ref,
                predicate=fact.predicate,
                object_text=fact.object_text,
                session_number=fact.session_number,
                secret=fact.secret,
            )
        )

    superseded: dict[str, list[dict]] = {}
    for fact in history:
        superseded.setdefault(fact.subject_ref, []).append(
            {
                "id": str(fact.id),
                "predicate": fact.predicate,
                "object_text": fact.object_text,
                "superseded_at_session": fact.superseded_at_session,
            }
        )

    return {
        "entities": [
            {
                **EntityOut(
                    ref=e.ref, kind=e.kind.value, name=e.name, summary=e.summary,
                    mentions=e.mentions, first_seen_session=e.first_seen_session,
                    state=e.state, status=e.status.value,
                    known_to_players=e.known_to_players, history=e.history,
                ).model_dump(),
                "facts": [f.model_dump() for f in by_subject.get(e.ref, [])],
                "superseded": superseded.get(e.ref, []),
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


class EntityUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    kind: str | None = Field(default=None, max_length=20)
    summary: str | None = Field(default=None, max_length=2000)
    known_to_players: bool | None = None


@router.patch("/entities/{entity_ref:path}")
async def update_entity(
    entity_ref: str,
    payload: EntityUpdate,
    campaign_id: uuid.UUID,
    db: DbDep,
    principal: PrincipalDep,
) -> dict:
    """Correct an entry.

    Extraction is a guess, and a wrong guess is visible to the player in the
    codex - so it has to be fixable without a database client.
    """
    _, user = await _authorize(db, principal, campaign_id)
    (await resolve_access(db, user.id, campaign_id, principal=principal)).require_gm()
    memory = MemoryService(db, campaign_id)
    entity = await memory.entity_by_ref(entity_ref)
    if entity is None:
        raise NotFoundError("entity not found")

    if payload.kind is not None:
        try:
            entity.kind = EntityKind(payload.kind)
        except ValueError as exc:
            raise InvalidRequest(f"unknown kind: {payload.kind}") from exc
    if payload.name is not None:
        entity.name = payload.name
        # The ref follows the name, so facts must be carried across or they
        # are orphaned - a fact pointing at a ref nobody has is invisible.
        new_ref = make_ref(entity.kind.value, payload.name)
        if new_ref != entity.ref:
            for fact in await memory.facts(subject_refs=[entity.ref]):
                fact.subject_ref = new_ref
            entity.ref = new_ref
    if payload.summary is not None:
        entity.summary = payload.summary
    if payload.known_to_players is not None:
        entity.known_to_players = payload.known_to_players

    await db.flush()
    return {"ref": entity.ref, "name": entity.name, "kind": entity.kind.value}


@router.delete("/entities/{entity_ref:path}", status_code=204)
async def delete_entity(
    entity_ref: str, campaign_id: uuid.UUID, db: DbDep, principal: PrincipalDep
) -> None:
    """Removes the entity and the facts hanging off it.

    Leaving the facts behind would put orphans in the model's context that
    nothing in the codex explains.
    """
    _, user = await _authorize(db, principal, campaign_id)
    (await resolve_access(db, user.id, campaign_id, principal=principal)).require_gm()
    memory = MemoryService(db, campaign_id)
    entity = await memory.entity_by_ref(entity_ref)
    if entity is None:
        return
    for fact in await memory.facts(subject_refs=[entity_ref]):
        await db.delete(fact)
    await db.delete(entity)


class MergeIn(BaseModel):
    into_ref: str = Field(max_length=160)


@router.post("/entities/{entity_ref:path}/merge")
async def merge_entity(
    entity_ref: str,
    payload: MergeIn,
    campaign_id: uuid.UUID,
    db: DbDep,
    principal: PrincipalDep,
) -> dict:
    """Fold one entry into another, carrying its facts and mentions.

    The common repair: extraction named the same person twice and their history
    is split across two entries.
    """
    _, user = await _authorize(db, principal, campaign_id)
    (await resolve_access(db, user.id, campaign_id, principal=principal)).require_gm()
    memory = MemoryService(db, campaign_id)
    source = await memory.entity_by_ref(entity_ref)
    target = await memory.entity_by_ref(payload.into_ref)
    if source is None or target is None:
        raise NotFoundError("entity not found")
    if source.ref == target.ref:
        raise InvalidRequest("cannot merge an entity into itself")

    for fact in await memory.facts(subject_refs=[source.ref]):
        fact.subject_ref = target.ref
    target.mentions += source.mentions
    if len(source.summary) > len(target.summary):
        target.summary = source.summary
    await db.delete(source)
    await db.flush()
    return {"ref": target.ref, "mentions": target.mentions}


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


# --------------------------------------------------------------- review queue
#
# Everything the model proposed and no human has ruled on. The contract that
# makes this bearable: approval governs DURABILITY, not availability. Within a
# session, continuity comes from the verbatim `recent` layer, which never
# touches the codex - so the model narrates freely off unapproved material and
# play never blocks on the queue. Rejecting an entry only means no future
# session is bound by it.


class PendingEntityOut(BaseModel):
    ref: str
    kind: str
    name: str
    summary: str
    mentions: int
    proposed_in_session: int | None


class PendingFactOut(BaseModel):
    id: uuid.UUID
    subject_ref: str
    predicate: str
    object_text: str
    session_number: int | None


@router.get("/pending")
async def pending(
    campaign_id: uuid.UUID,
    db: DbDep,
    principal: PrincipalDep,
    session_number: int | None = None,
) -> dict:
    _, user = await _authorize(db, principal, campaign_id)
    (await resolve_access(db, user.id, campaign_id, principal=principal)).require_gm()
    queue = await MemoryService(db, campaign_id).pending(
        session_number=session_number
    )
    return {
        "entities": [
            PendingEntityOut(
                ref=e.ref, kind=e.kind.value, name=e.name, summary=e.summary,
                mentions=e.mentions, proposed_in_session=e.proposed_in_session,
            ).model_dump()
            for e in queue["entities"]
        ],
        "facts": [
            PendingFactOut(
                id=f.id, subject_ref=f.subject_ref, predicate=f.predicate,
                object_text=f.object_text, session_number=f.session_number,
            ).model_dump()
            for f in queue["facts"]
        ],
    }


class ReviewIn(BaseModel):
    """Either specific refs/ids, or everything - both granularities, because
    triaging forty entries one at a time is how a queue stops being used, and
    bulk-accepting everything is how bad entries get in."""

    entity_refs: list[str] = Field(default_factory=list)
    fact_ids: list[uuid.UUID] = Field(default_factory=list)
    all_pending: bool = False
    session_number: int | None = None


@router.post("/pending/accept")
async def accept_pending(
    campaign_id: uuid.UUID, payload: ReviewIn, db: DbDep, principal: PrincipalDep
) -> dict:
    _, user = await _authorize(db, principal, campaign_id)
    (await resolve_access(db, user.id, campaign_id, principal=principal)).require_gm()
    memory = MemoryService(db, campaign_id)

    entities, facts = await _selection(memory, payload)
    for entity in entities:
        entity.status = EntryStatus.ACCEPTED
    for fact in facts:
        fact.status = EntryStatus.ACCEPTED
    await db.flush()
    return {"accepted_entities": len(entities), "accepted_facts": len(facts)}


@router.post("/pending/reject")
async def reject_pending(
    campaign_id: uuid.UUID, payload: ReviewIn, db: DbDep, principal: PrincipalDep
) -> dict:
    """Rejection means "not now", so the rows are deleted rather than
    tombstoned. If the thing genuinely matters it will be mentioned again and
    proposed again, which is the behaviour we want anyway - a tombstone would
    permanently blind extraction to something that might become important
    three sessions later."""
    _, user = await _authorize(db, principal, campaign_id)
    (await resolve_access(db, user.id, campaign_id, principal=principal)).require_gm()
    memory = MemoryService(db, campaign_id)

    entities, facts = await _selection(memory, payload)
    for fact in facts:
        await db.delete(fact)
    for entity in entities:
        # Facts hanging off a rejected entity have nothing to attach to.
        for orphan in await memory.facts(
            subject_refs=[entity.ref], status=None, include_superseded=True
        ):
            await db.delete(orphan)
        await db.delete(entity)
    await db.flush()
    return {"rejected_entities": len(entities), "rejected_facts": len(facts)}


async def _selection(memory: MemoryService, payload: ReviewIn):
    queue = await memory.pending(session_number=payload.session_number)
    if payload.all_pending:
        return queue["entities"], queue["facts"]
    refs = set(payload.entity_refs)
    ids = set(payload.fact_ids)
    return (
        [e for e in queue["entities"] if e.ref in refs],
        [f for f in queue["facts"] if f.id in ids],
    )


# ------------------------------------------------------------- fact editing


class FactIn(BaseModel):
    subject_ref: str = Field(max_length=160)
    predicate: str = Field(max_length=120)
    object_text: str = Field(max_length=4000)
    secret: bool = False
    session_number: int | None = None


@router.post("/facts", status_code=201)
async def create_fact(
    campaign_id: uuid.UUID, payload: FactIn, db: DbDep, principal: PrincipalDep
) -> dict:
    """A fact written by hand is canon immediately - the GM is the authority
    the queue exists to defer to, so making them approve their own writing
    would be theatre."""
    _, user = await _authorize(db, principal, campaign_id)
    (await resolve_access(db, user.id, campaign_id, principal=principal)).require_gm()
    fact = await MemoryService(db, campaign_id).add_fact(
        subject_ref=payload.subject_ref,
        predicate=payload.predicate,
        object_text=payload.object_text,
        secret=payload.secret,
        session_number=payload.session_number,
        status=EntryStatus.ACCEPTED,
    )
    if fact is None:
        raise InvalidRequest("that fact is already on record")
    return {"id": str(fact.id)}


class FactPatch(BaseModel):
    predicate: str | None = Field(default=None, max_length=120)
    object_text: str | None = Field(default=None, max_length=4000)
    secret: bool | None = None


@router.patch("/facts/{fact_id}")
async def update_fact(
    fact_id: uuid.UUID,
    payload: FactPatch,
    campaign_id: uuid.UUID,
    db: DbDep,
    principal: PrincipalDep,
) -> dict:
    """Correction, not transformation. Use this when the record is WRONG -
    the edit leaves no trace, because there is no history worth keeping in a
    mistake. When the world changed instead, use supersede."""
    _, user = await _authorize(db, principal, campaign_id)
    (await resolve_access(db, user.id, campaign_id, principal=principal)).require_gm()
    fact = await db.get(CanonFact, fact_id)
    if fact is None or fact.campaign_id != campaign_id:
        raise NotFoundError("fact not found")
    if payload.predicate is not None:
        fact.predicate = payload.predicate
    if payload.object_text is not None:
        fact.object_text = payload.object_text
    if payload.secret is not None:
        fact.secret = payload.secret
    await db.flush()
    return {"id": str(fact.id)}


class SupersedeIn(BaseModel):
    object_text: str = Field(max_length=4000)
    predicate: str | None = Field(default=None, max_length=120)
    session_number: int | None = None


@router.post("/facts/{fact_id}/supersede")
async def supersede_fact(
    fact_id: uuid.UUID,
    payload: SupersedeIn,
    campaign_id: uuid.UUID,
    db: DbDep,
    principal: PrincipalDep,
) -> dict:
    """The world changed. Borveld ran an inn; now he is a lich.

    The old fact is kept and marked rather than edited away, because the
    townspeople remember the inn and so should the model. Rendered to it under
    "no longer true", which is also what stops the pair reading as a
    contradiction.
    """
    _, user = await _authorize(db, principal, campaign_id)
    (await resolve_access(db, user.id, campaign_id, principal=principal)).require_gm()
    replacement = await MemoryService(db, campaign_id).supersede(
        fact_id,
        predicate=payload.predicate,
        object_text=payload.object_text,
        session_number=payload.session_number,
    )
    if replacement is None:
        raise NotFoundError("fact not found")
    return {"id": str(replacement.id), "supersedes": str(fact_id)}


@router.delete("/facts/{fact_id}", status_code=204)
async def retract_fact(
    fact_id: uuid.UUID, campaign_id: uuid.UUID, db: DbDep, principal: PrincipalDep
) -> None:
    """This was never true. Soft-deleted so it can still be traced to the turn
    that produced it, but the model never sees it again."""
    _, user = await _authorize(db, principal, campaign_id)
    (await resolve_access(db, user.id, campaign_id, principal=principal)).require_gm()
    fact = await db.get(CanonFact, fact_id)
    if fact is None or fact.campaign_id != campaign_id:
        return
    await MemoryService(db, campaign_id).retract(fact_id)


# ------------------------------------------------------- entity transformation


class TransformIn(BaseModel):
    summary: str = Field(max_length=2000)
    note: str = Field(default="", max_length=400)
    session_number: int | None = None


@router.post("/entities/{entity_ref:path}/transform")
async def transform_entity(
    entity_ref: str,
    payload: TransformIn,
    campaign_id: uuid.UUID,
    db: DbDep,
    principal: PrincipalDep,
) -> dict:
    """Change what an entry says while keeping what it used to say.

    The distinction from PATCH is the one that matters in play: PATCH is for
    when extraction got it wrong and the old text should vanish. This is for
    when the world moved and the old text is history worth keeping.
    """
    _, user = await _authorize(db, principal, campaign_id)
    (await resolve_access(db, user.id, campaign_id, principal=principal)).require_gm()
    entity = await MemoryService(db, campaign_id).transform_entity(
        entity_ref,
        summary=payload.summary,
        note=payload.note,
        session_number=payload.session_number,
    )
    if entity is None:
        raise NotFoundError("entity not found")
    return {"ref": entity.ref, "summary": entity.summary, "history": entity.history}


class SplitIn(BaseModel):
    name: str = Field(max_length=200)
    kind: str = Field(default="npc", max_length=20)
    summary: str = Field(default="", max_length=2000)
    fact_ids: list[uuid.UUID] = Field(default_factory=list)


@router.post("/entities/{entity_ref:path}/split")
async def split_entity(
    entity_ref: str,
    payload: SplitIn,
    campaign_id: uuid.UUID,
    db: DbDep,
    principal: PrincipalDep,
) -> dict:
    """Pull a wrongly-merged entity back out into its own entry.

    The counterpart to merge, and the reason it needs to exist: `_find_similar`
    folds near-duplicates together automatically, its own comment concedes a
    merge cannot be undone from the UI, and "the guard" matching "the gate
    guard" is a coin flip about whether they are one person. Without split, one
    bad automatic merge is permanent.
    """
    _, user = await _authorize(db, principal, campaign_id)
    (await resolve_access(db, user.id, campaign_id, principal=principal)).require_gm()
    memory = MemoryService(db, campaign_id)
    source = await memory.entity_by_ref(entity_ref)
    if source is None:
        raise NotFoundError("entity not found")

    created = await memory.upsert_entity(
        kind=payload.kind,
        name=payload.name,
        summary=payload.summary,
        status=EntryStatus.ACCEPTED,
    )
    if created.ref == source.ref:
        raise InvalidRequest("the new entry must have a different name")

    moved = 0
    if payload.fact_ids:
        for fact in await memory.facts(
            subject_refs=[source.ref], status=None, include_superseded=True
        ):
            if fact.id in set(payload.fact_ids):
                fact.subject_ref = created.ref
                moved += 1
    # Mentions were pooled by the bad merge; halving is a guess, but leaving
    # them all on the original overstates it and retrieval ranks on this.
    if source.mentions > 1:
        source.mentions = max(1, source.mentions // 2)
        created.mentions = source.mentions
    await db.flush()
    return {"ref": created.ref, "moved_facts": moved}
