"""Play endpoints.

The turn endpoint streams. A turn takes several seconds of tool calls before
any prose exists, and a player watching a spinner for four seconds experiences
that very differently from a player watching events arrive - so mechanics are
emitted as they resolve, then narration streams token by token.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.auth.deps import DbDep, PrincipalDep
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.modules.campaigns.models import Campaign, CampaignMember
from app.modules.characters.models import Character as CharacterRow
from app.modules.identity.service import IdentityService
from app.modules.narrative.turn_loop import TurnInput, TurnLoop, strip_repetition
from app.modules.sessions.models import PlaySession, SessionStatus, Turn
from app.modules.sessions.service import SessionService
from app.modules.memory.extraction import Extractor
from app.modules.memory.service import DatabaseContextSource, MemoryService
from app.modules.memory.summarize import Summarizer
from app.platform.ai.context import ContextBuilder

router = APIRouter()


class SessionOut(BaseModel):
    id: uuid.UUID
    campaign_id: uuid.UUID
    number: int
    status: str
    scene_id: str
    active_character_id: uuid.UUID | None
    title: str | None = None
    summary: str | None = None
    created_at: datetime | None = None
    ended_at: datetime | None = None
    turn_count: int = 0

    model_config = {"from_attributes": True}


class SessionUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=4000)


class StartSession(BaseModel):
    campaign_id: uuid.UUID


class TurnIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    # Omitted or null means the input is addressed to the whole party.
    actor_id: uuid.UUID | None = None


class SpotlightIn(BaseModel):
    character_id: uuid.UUID | None = None


async def _authorize(db, principal, campaign_id: uuid.UUID) -> Campaign:
    """Membership check. Every play endpoint goes through this."""
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
    return campaign


@router.post("", response_model=SessionOut, status_code=201)
async def start_session(
    payload: StartSession, request: Request, db: DbDep, principal: PrincipalDep
) -> PlaySession:
    campaign = await _authorize(db, principal, payload.campaign_id)
    service = SessionService(db)
    session = await service.start(campaign.id, ruleset_id=campaign.ruleset_id)

    # Set the scene before the player has to. A blank screen and a cursor asks
    # them to do the game master's job.
    party = await service.party(campaign.id)
    if party:
        memory = MemoryService(db, campaign.id)
        source = DatabaseContextSource(
            memory,
            previous=await service.previous_session_exchanges(
                campaign.id, session.number
            ),
        )
        await source.preload()

        engine = await service.engine_for(session, campaign.ruleset_id)
        opening = await TurnLoop(request.app.state.ai, engine).open_scene(
            TurnInput(
                session_id=str(session.id),
                scene_id=session.scene_id,
                text=campaign.premise or "",
                actor_id=None,
                party=party,
                context=ContextBuilder().build(
                    source, str(session.id), session.scene_id
                ),
                tone=(campaign.settings or {}).get("tone", "Grounded and grim."),
            )
        )
        if opening:
            await service.record_turn(
                session,
                actor_id=None,
                player_input="",
                narration=opening,
                tool_calls=[],
                deltas=[],
                prompt_version="open_scene",
            )

    return session


@router.get("", response_model=list[SessionOut])
async def list_sessions(
    campaign_id: uuid.UUID, db: DbDep, principal: PrincipalDep
) -> list[SessionOut]:
    """Sessions for a campaign, newest first, with turn counts.

    Counted in one grouped query rather than per row - a campaign with forty
    sessions should not cost forty round trips to render a list.
    """
    await _authorize(db, principal, campaign_id)
    result = await db.execute(
        select(PlaySession)
        .where(PlaySession.campaign_id == campaign_id)
        .order_by(PlaySession.number.desc())
    )
    sessions = list(result.scalars())

    counts = dict(
        (
            await db.execute(
                select(Turn.session_id, func.count(Turn.id))
                .where(Turn.session_id.in_([s.id for s in sessions] or [None]))
                .group_by(Turn.session_id)
            )
        ).all()
    )

    return [
        SessionOut(
            id=s.id, campaign_id=s.campaign_id, number=s.number,
            status=s.status.value if hasattr(s.status, "value") else str(s.status),
            scene_id=s.scene_id, active_character_id=s.active_character_id,
            title=s.title, summary=s.summary,
            created_at=s.created_at, ended_at=s.ended_at,
            turn_count=counts.get(s.id, 0),
        )
        for s in sessions
    ]


@router.patch("/{session_id}", response_model=SessionOut)
async def rename_session(
    session_id: uuid.UUID, payload: SessionUpdate, db: DbDep, principal: PrincipalDep
) -> PlaySession:
    service = SessionService(db)
    session = await service.get(session_id)
    await _authorize(db, principal, session.campaign_id)
    if payload.title is not None:
        session.title = payload.title
    if payload.summary is not None:
        session.summary = payload.summary
    await db.flush()
    return session


@router.delete("/{session_id}", status_code=204)
async def delete_session(
    session_id: uuid.UUID, db: DbDep, principal: PrincipalDep
) -> None:
    """Removes a session and its turns.

    Refuses while the session is live: deleting the thing you are currently
    playing is never what was meant, and end-then-delete is one extra click.
    Journal notes survive - they belong to the player, not the session, and
    losing your own writing to a cleanup action would be indefensible.
    """
    service = SessionService(db)
    session = await service.get(session_id)
    await _authorize(db, principal, session.campaign_id)
    if session.status == SessionStatus.ACTIVE:
        raise ConflictError(
            "end the session before deleting it",
            detail={"hint": "an active session is the one you are playing"},
        )
    await db.delete(session)


@router.get("/{session_id}", response_model=SessionOut)
async def get_session(
    session_id: uuid.UUID, db: DbDep, principal: PrincipalDep
) -> PlaySession:
    service = SessionService(db)
    session = await service.get(session_id)
    await _authorize(db, principal, session.campaign_id)
    return session


@router.post("/{session_id}/spotlight", response_model=SessionOut)
async def set_spotlight(
    session_id: uuid.UUID, payload: SpotlightIn, db: DbDep, principal: PrincipalDep
) -> PlaySession:
    """Choose who acts. Null hands the spotlight to the party as a whole."""
    service = SessionService(db)
    session = await service.get(session_id)
    await _authorize(db, principal, session.campaign_id)
    return await service.set_spotlight(session_id, payload.character_id)


@router.post("/{session_id}/end", response_model=SessionOut)
async def end_session(
    session_id: uuid.UUID, request: Request, db: DbDep, principal: PrincipalDep
) -> PlaySession:
    """Seals the log and writes a summary.

    The summary is what lets the next session load this one as a paragraph
    instead of a transcript - it is the compression rung that keeps context
    flat as a campaign gets long.
    """
    service = SessionService(db)
    session = await service.get(session_id)
    await _authorize(db, principal, session.campaign_id)

    exchanges = await service.recent_exchanges(session.id, limit=400)
    summary = await Summarizer(
        request.app.state.ai, MemoryService(db, session.campaign_id)
    ).summarize_session(
        session_id=str(session.id),
        session_number=session.number,
        exchanges=exchanges,
    )

    return await service.end(session_id, summary=summary)


@router.get("/{session_id}/turns")
async def list_turns(
    session_id: uuid.UUID, db: DbDep, principal: PrincipalDep, limit: int = 100
) -> list[dict]:
    service = SessionService(db)
    session = await service.get(session_id)
    await _authorize(db, principal, session.campaign_id)
    result = await db.execute(
        select(Turn).where(Turn.session_id == session_id)
        .order_by(Turn.ordinal).limit(limit)
    )
    return [
        {
            "ordinal": t.ordinal,
            "actor_id": str(t.actor_id) if t.actor_id else None,
            "player_input": t.player_input,
            "narration": t.narration,
            "tool_calls": t.tool_calls,
        }
        for t in result.scalars()
    ]


@router.post("/{session_id}/turn")
async def take_turn(
    session_id: uuid.UUID,
    payload: TurnIn,
    request: Request,
    db: DbDep,
    principal: PrincipalDep,
) -> StreamingResponse:
    service = SessionService(db)
    session = await service.get(session_id)
    campaign = await _authorize(db, principal, session.campaign_id)

    party = await service.party(campaign.id)
    if not party:
        raise NotFoundError("this campaign has no characters yet")

    actor_id = payload.actor_id or session.active_character_id
    if actor_id is not None and str(actor_id) not in party:
        raise NotFoundError("that character is not in the active party")

    clocks = await service.clocks(campaign.id)
    engine = await service.engine_for(session, campaign.ruleset_id)

    # Real memory. Retrieval is ranked and the builder caps each layer, so
    # this costs about the same at session 40 as at session 4.
    memory = MemoryService(db, campaign.id)
    source = DatabaseContextSource(
        memory,
        in_play_refs=[],
        previous=await service.previous_session_exchanges(campaign.id, session.number),
        recent=await service.recent_exchanges(session.id),
    )
    await source.preload()
    context = ContextBuilder().build(source, str(session.id), session.scene_id)

    turn_input = TurnInput(
        session_id=str(session.id),
        scene_id=session.scene_id,
        text=payload.text,
        actor_id=str(actor_id) if actor_id else None,
        party=party,
        clocks=clocks,
        context=context,
        tone=(campaign.settings or {}).get("tone", "Grounded and grim."),
    )

    loop = TurnLoop(request.app.state.ai, engine)

    async def events() -> AsyncIterator[str]:
        """Server-sent events.

        Mechanics are emitted as they resolve so the player sees dice land
        before the prose describing them arrives. Chosen over websockets here
        because a solo turn is one-directional; Milestone 5 adds websockets
        when multiplayer needs a bidirectional channel.
        """
        try:
            outcome = await loop.run(turn_input)
        except Exception as exc:  # surfaced to the client, not swallowed
            yield _sse("error", {"message": str(exc)})
            return

        for call in outcome.tool_calls:
            yield _sse("mechanic", call)

        # Belt and braces. The turn loop already cleans this; doing it here too
        # means a future narration path that forgets cannot write spam to disk.
        narration = strip_repetition(outcome.narration)
        for chunk in _paragraphs(narration):
            yield _sse("narration", {"text": chunk})

        yield _sse("state", {
            "party": {
                cid: {
                    "name": c.name, "hp": c.hp, "hp_max": c.hp_max,
                    "stress": c.stress, "stress_max": c.stress_max,
                    "conditions": [x.spec_id for x in c.conditions],
                    "inventory": list(c.inventory),
                }
                for cid, c in outcome.party_after.items()
            },
            "clocks": {
                k: {"label": v.label, "filled": v.filled, "size": v.size}
                for k, v in outcome.clocks.items() if not v.hidden
            },
        })

        # Extraction runs after the player has their narration. If it fails,
        # the turn still stands - losing one turn's memory is far cheaper than
        # losing the turn, and named things recur.
        extraction = await Extractor(request.app.state.ai).extract(
            narration, session_id=str(session.id)
        )

        await service.save_party(outcome.party_after)
        await service.save_clocks(campaign.id, outcome.clocks)
        await service.persist_locks(campaign.id, engine.ledger)
        turn = await service.record_turn(
            session,
            actor_id=actor_id,
            player_input=payload.text,
            narration=narration,
            tool_calls=outcome.tool_calls,
            deltas=[
                {"actor_id": k, "hp": d.hp, "stress": d.stress}
                for k, d in outcome.deltas.items()
            ],
            prompt_version=outcome.prompt_version,
        )

        for entity in extraction.entities:
            await memory.upsert_entity(
                kind=entity.kind, name=entity.name, summary=entity.summary,
                session_number=session.number,
            )
        for fact in extraction.facts:
            await memory.add_fact(
                subject_ref=await memory.resolve_target(fact.subject),
                predicate=fact.predicate,
                object_text=fact.object,
                session_number=session.number,
                turn_id=turn.id,
            )

        await db.commit()
        yield _sse("learned", {
            "entities": [
                {"kind": e.kind, "name": e.name} for e in extraction.entities
            ],
            "facts": len(extraction.facts),
        })
        yield _sse("done", {"rejected": outcome.rejected})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _paragraphs(text: str) -> list[str]:
    return [p for p in text.split("\n\n") if p.strip()] or ([text] if text else [])
