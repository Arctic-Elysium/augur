"""Play endpoints.

The turn endpoint streams. A turn takes several seconds of tool calls before
any prose exists, and a player watching a spinner for four seconds experiences
that very differently from a player watching events arrive - so mechanics are
emitted as they resolve, then narration streams token by token.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterator
from datetime import datetime

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.auth.deps import DbDep, PrincipalDep, is_platform_admin
from app.core.config.settings import get_settings
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.modules.campaigns.models import Campaign, CampaignMember
from app.modules.characters.models import Character as CharacterRow
from app.modules.identity.service import IdentityService
from app.modules.narrative.turn_loop import TurnInput, TurnLoop, strip_repetition
from app.modules.sessions.models import PlaySession, SessionStatus, Turn
from app.modules.sessions.service import SessionService
from app.modules.memory.extraction import Extractor
from app.modules.campaigns.access import resolve_access
from app.modules.memory.service import DatabaseContextSource, MemoryService
from app.modules.memory.summarize import Summarizer
from app.platform.ai.context import ContextBuilder

router = APIRouter()


class SessionOut(BaseModel):
    id: uuid.UUID
    campaign_id: uuid.UUID
    number: int
    status: str
    destination: str | None = None
    pressure: str = "light"
    destination_clock_id: str | None = None
    destination_reached: bool = False
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
    campaign, _ = await campaign_for(db, principal, campaign_id)
    return campaign


async def _authorize_user(db, principal, campaign_id: uuid.UUID):
    """Like `_authorize`, but also hands back the user - needed wherever a
    GM-only action has to resolve access rights."""
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
                    source, str(session.id), session.scene_id,
                    primer=(campaign.settings or {}).get("primer", ""),
                    arc=(campaign.settings or {}).get("arc", ""),
                    directives=list(
                        (campaign.settings or {}).get("directives", [])
                    ),
                    destination=session.destination or "",
                    pressure=session.pressure or "light",
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
            destination=s.destination, pressure=s.pressure,
            destination_clock_id=s.destination_clock_id,
            destination_reached=s.destination_reached,
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


class DestinationIn(BaseModel):
    destination: str | None = Field(default=None, max_length=4000)
    pressure: str | None = Field(default=None, pattern="^(off|light|firm)$")
    destination_clock_id: str | None = Field(default=None, max_length=120)
    destination_reached: bool | None = None


@router.patch("/{session_id}/destination", response_model=SessionOut)
async def set_destination(
    session_id: uuid.UUID,
    payload: DestinationIn,
    db: DbDep,
    principal: PrincipalDep,
) -> PlaySession:
    """Where this sitting should end up, and how hard to steer.

    GM-only: this is the strongest single lever on what the model does, and
    handing it to every player at a table would mean four people quietly
    pulling the session in four directions.
    """
    service = SessionService(db)
    session = await service.get(session_id)
    _, user = await _authorize_user(db, principal, session.campaign_id)
    (await resolve_access(db, user.id, session.campaign_id, principal=principal)).require_gm()

    if payload.destination is not None:
        session.destination = payload.destination.strip() or None
    if payload.pressure is not None:
        session.pressure = payload.pressure
    if payload.destination_clock_id is not None:
        session.destination_clock_id = payload.destination_clock_id or None
    if payload.destination_reached is not None:
        session.destination_reached = payload.destination_reached
    await db.flush()
    return session


class AmendIn(BaseModel):
    narration: str = Field(min_length=1, max_length=20_000)


@router.patch("/turns/{turn_id}/narration", response_model=dict)
async def amend_turn(
    turn_id: uuid.UUID, payload: AmendIn, db: DbDep, principal: PrincipalDep
) -> dict:
    """Rewrite what the model said. The record is the GM's.

    The cheapest correction there is - no model call, no cost, instant - and
    the most direct expression of the GM hat: what is stored is what you
    wrote, and that is what every later turn reads as context.
    """
    turn = await db.get(Turn, turn_id)
    if turn is None:
        raise NotFoundError("turn not found")
    session = await SessionService(db).get(turn.session_id)
    _, user = await _authorize_user(db, principal, session.campaign_id)
    (await resolve_access(db, user.id, session.campaign_id, principal=principal)).require_gm()

    turn.narration = payload.narration.strip()
    turn.prompt_version = f"{turn.prompt_version}+amended"
    await db.flush()
    return {"id": str(turn.id), "narration": turn.narration}


class RedoIn(BaseModel):
    note: str = Field(default="", max_length=2000)


@router.post("/turns/{turn_id}/redo", response_model=dict)
async def redo_turn(
    turn_id: uuid.UUID, request: Request, db: DbDep, principal: PrincipalDep,
    payload: RedoIn | None = None,
) -> dict:
    """Regenerate the prose for a turn. The mechanics stand.

    Deliberately narrow: the dice, the damage and the check locks all persist
    and only the narration is rewritten. If redo re-rolled, it would be a
    retry-farm with a friendly name - you could reroll any failed check by
    calling it drift, which is precisely what the check ledger exists to stop.
    """
    turn = await db.get(Turn, turn_id)
    if turn is None:
        raise NotFoundError("turn not found")
    service = SessionService(db)
    session = await service.get(turn.session_id)
    campaign, user = await _authorize_user(db, principal, session.campaign_id)
    (await resolve_access(db, user.id, session.campaign_id, principal=principal)).require_gm()

    party = await service.party(campaign.id)
    engine = await service.engine_for(session, campaign.ruleset_id)
    memory = MemoryService(db, campaign.id)
    source = DatabaseContextSource(
        memory,
        previous=await service.previous_session_exchanges(
            campaign.id, session.number
        ),
        recent=await service.recent_exchanges(session.id),
    )
    await source.preload()
    settings = campaign.settings or {}

    resolved = "\n".join(
        f"- {c['name']}: {c['result']}"
        for c in (turn.tool_calls or []) if c.get("ok")
    )
    note = (payload.note if payload else "") or ""
    instruction = (
        f"{turn.player_input}\n\n"
        "[Rewrite the narration for this turn. These mechanics already "
        f"resolved - narrate them, do not re-roll]\n{resolved or 'none'}"
    )
    if note:
        instruction += (
            f"\n\n[The game master rejected your previous narration: {note}]"
        )

    loop = TurnLoop(request.app.state.ai, engine)
    narration = await loop.open_scene(
        TurnInput(
            session_id=str(session.id),
            scene_id=session.scene_id,
            text=instruction,
            actor_id=str(turn.actor_id) if turn.actor_id else None,
            party=party,
            context=ContextBuilder().build(
                source, str(session.id), session.scene_id,
                primer=settings.get("primer", ""),
                arc=settings.get("arc", ""),
                directives=[*settings.get("directives", []), *([note] if note else [])],
                destination=session.destination or "",
                pressure=session.pressure or "light",
            ),
            tone=settings.get("tone", "Grounded and grim."),
        )
    )
    turn.narration = strip_repetition(narration)
    turn.prompt_version = f"{turn.prompt_version}+redo"
    await db.flush()
    await db.commit()
    return {"id": str(turn.id), "narration": turn.narration}


@router.get("/{session_id}/turns")
async def list_turns(
    session_id: uuid.UUID, db: DbDep, principal: PrincipalDep, limit: int = 2000
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
            # The id is what amend and redo address. Without it the client can
            # render a turn it has no way to correct.
            "id": str(t.id),
            "ordinal": t.ordinal,
            "actor_id": str(t.actor_id) if t.actor_id else None,
            "player_input": t.player_input,
            "narration": t.narration,
            "tool_calls": t.tool_calls,
        }
        for t in result.scalars()
    ]


@router.get("/{session_id}/export")
async def export_session(
    session_id: uuid.UUID, db: DbDep, principal: PrincipalDep, format: str = "md"
) -> Response:
    """The full log, including the mechanics.

    Written for reading back a session to find where the fiction drifted, which
    means it has to show what the engine actually did alongside what the model
    said - a transcript of only the prose hides the thing you are looking for.
    """
    service = SessionService(db)
    session = await service.get(session_id)
    campaign = await _authorize(db, principal, session.campaign_id)

    result = await db.execute(
        select(Turn).where(Turn.session_id == session_id).order_by(Turn.ordinal)
    )
    turns = list(result.scalars())

    if format == "prompts":
        # The debug export: what was actually assembled and sent, turn by
        # turn. Only populated for turns played while `debug_prompts` was on,
        # so an empty file means the flag was off, not that nothing happened.
        captured = [t for t in turns if t.prompt_debug]
        lines = [
            f"# {campaign.name} — Session {session.number} — prompt capture",
            "",
            f"{len(captured)} of {len(turns)} turns captured.",
            "",
        ]
        if not captured:
            lines.append(
                "No turns were captured. Turn on `debug_prompts` in campaign "
                "settings and play a turn; capture is not retroactive."
            )
        for turn in captured:
            debug = turn.prompt_debug or {}
            lines += [
                f"## Turn {turn.ordinal}",
                "",
                f"- prompt version: `{debug.get('prompt_version', '')}`",
                f"- context tokens: {debug.get('context_tokens', 0)}",
                f"- truncated layers: `{json.dumps(debug.get('truncated', {}))}`",
                "",
                "### Player input",
                "",
                f"> {debug.get('player_input', '')}",
                "",
            ]
            if debug.get("ooc"):
                lines += ["### Out of character", "", f"> {debug['ooc']}", ""]
            lines += [
                "### Assembled context",
                "",
                "```",
                str(debug.get("context", "")),
                "```",
                "",
            ]
        return Response(
            content="\n".join(lines),
            media_type="text/markdown",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{campaign.name}-session-'
                    f'{session.number}-prompts.md"'
                )
            },
        )

    if format == "json":
        return Response(
            content=json.dumps(
                {
                    "campaign": campaign.name,
                    "session": session.number,
                    "title": session.title,
                    "summary": session.summary,
                    "seed": session.seed,
                    "turns": [
                        {
                            "ordinal": t.ordinal,
                            "actor_id": str(t.actor_id) if t.actor_id else None,
                            "input": t.player_input,
                            "narration": t.narration,
                            "tool_calls": t.tool_calls,
                            "deltas": t.deltas,
                            "prompt_version": t.prompt_version,
                            "at": t.created_at.isoformat() if t.created_at else None,
                        }
                        for t in turns
                    ],
                },
                indent=2,
            ),
            media_type="application/json",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{campaign.name}-session-{session.number}.json"'
                )
            },
        )

    lines = [
        f"# {campaign.name} — Session {session.number}",
        "",
        f"*{session.title}*" if session.title else "",
        # The seed makes every roll in this log reproducible, which is what
        # turns "that felt wrong" into something checkable.
        f"Seed `{session.seed}` · {len(turns)} turns",
        "",
    ]
    for turn in turns:
        if turn.player_input:
            lines += [f"## Turn {turn.ordinal}", "", f"> {turn.player_input}", ""]
        for call in turn.tool_calls or []:
            mark = "" if call.get("ok") else "REFUSED "
            lines.append(
                f"`{mark}{call.get('name')}` "
                f"`{json.dumps(call.get('arguments', {}), separators=(',', ':'))}` "
                f"→ `{json.dumps(call.get('result', {}), separators=(',', ':'))}`"
            )
        if turn.tool_calls:
            lines.append("")
        if turn.narration:
            lines += [turn.narration, ""]
        if turn.prompt_version:
            lines += [f"<sub>prompt {turn.prompt_version}</sub>", ""]

    if session.summary:
        lines += ["---", "", "## Summary", "", session.summary, ""]

    return Response(
        content="\n".join(lines),
        media_type="text/markdown",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{campaign.name}-session-{session.number}.md"'
            )
        },
    )


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
    settings = campaign.settings or {}
    context = ContextBuilder().build(
        source, str(session.id), session.scene_id,
        primer=settings.get("primer", ""),
        arc=settings.get("arc", ""),
        directives=list(settings.get("directives", [])),
        destination=session.destination or "",
        pressure=session.pressure or "light",
        pacing=_pacing_note(session, clocks),
    )

    # ((double parens)) is table talk - the player addressing the GM, not the
    # world. Stripped from the fiction here; delivered to the model as a
    # separate channel; stored verbatim so the log shows what was said.
    fiction, ooc = _split_ooc(payload.text)

    known_entities = await memory.entities()
    established = tuple(e.name for e in known_entities) + tuple(
        c.name for c in party.values()
    )

    turn_input = TurnInput(
        session_id=str(session.id),
        scene_id=session.scene_id,
        text=fiction,
        actor_id=str(actor_id) if actor_id else None,
        party=party,
        clocks=clocks,
        context=context,
        tone=(campaign.settings or {}).get("tone", "Grounded and grim."),
        ooc=ooc,
        established_refs=established,
        allow_player_grants=settings.get("allow_player_grants", True),
    )

    loop = TurnLoop(request.app.state.ai, engine)

    async def events() -> AsyncIterator[str]:
        """Server-sent events.

        Mechanics are emitted as they resolve so the player sees dice land
        before the prose describing them arrives. Chosen over websockets here
        because a solo turn is one-directional; Milestone 5 adds websockets
        when multiplayer needs a bidirectional channel.

        The turn is persisted and committed BEFORE the first byte streams. A
        disconnect mid-stream used to garbage-collect this generator with the
        turn unrecorded - dice rolled, damage dealt, check locks taken, none
        of it durable, and a player who noticed could retry-farm a roll by
        pulling the plug. Now the client losing the stream costs a refetch,
        never the turn.
        """
        try:
            outcome = await loop.run(turn_input)
        except Exception as exc:  # surfaced to the client, not swallowed
            yield _sse("error", {"message": str(exc)})
            return

        narration = strip_repetition(outcome.narration)

        debug = None
        if settings.get("debug_prompts"):
            # The assembled packet, not the rendered system prompt: the prompt
            # is mostly an identical cached prefix every turn, and storing it
            # 400 times is how you get a 40MB session export that answers
            # nothing. The packet is the part that actually varies.
            debug = {
                "context": context.render(),
                "context_tokens": context.estimated_tokens(),
                "truncated": context.truncated,
                "player_input": payload.text,
                "fiction": fiction,
                "ooc": ooc,
                "prompt_version": outcome.prompt_version,
            }

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
                {"actor_id": k, "hp": d.hp}
                for k, d in outcome.deltas.items()
            ],
            prompt_version=outcome.prompt_version,
            prompt_debug=debug,
        )
        await db.commit()

        for call in outcome.tool_calls:
            yield _sse("mechanic", call)

        for chunk in _paragraphs(narration):
            yield _sse("narration", {"text": chunk})

        yield _sse("state", {
            "party": {
                cid: {
                    "name": c.name, "hp": c.hp, "hp_max": c.hp_max,
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

        # Extraction after the turn is durable and the player has their
        # narration. If it fails - or the client disconnects here - the turn
        # still stands: losing one turn's memory is far cheaper than losing
        # the turn, and named things recur.
        extraction = await Extractor(request.app.state.ai).extract(
            narration,
            session_id=str(session.id),
            # Already ordered by mentions descending, which is what the
            # extractor's roster truncation needs to be honest.
            known=[e.name for e in known_entities],
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


def _pacing_note(session, clocks: dict) -> str:
    """Turn a bound clock into a sentence about how much session is left.

    The model has no sense of duration - it will spend six turns on a
    conversation in a session meant to end at the docks. A clock it can read
    gives it the one thing it cannot infer: how much room is left.
    """
    clock_id = getattr(session, "destination_clock_id", None)
    if not clock_id:
        return ""
    clock = clocks.get(clock_id)
    if clock is None or not clock.size:
        return ""
    remaining = max(0, clock.size - clock.filled)
    if remaining == 0:
        return "The session is at its end. Bring it there now."
    share = clock.filled / clock.size
    if share < 0.34:
        return f"Early. {remaining} segments left before this session should close."
    if share < 0.75:
        return (
            f"Past the middle. {remaining} segments left - start closing "
            "threads rather than opening them."
        )
    return (
        f"Nearly done: {remaining} segments. Converge. Do not start anything "
        "that cannot finish."
    )


def _split_ooc(text: str) -> tuple[str, str]:
    """Split ((table talk)) out of player input.

    Returns (fiction, ooc). Unclosed parens are left alone - a player typing
    "((" mid-sentence should not have half their message eaten.
    """
    ooc_parts = re.findall(r"\(\((.+?)\)\)", text, flags=re.DOTALL)
    fiction = re.sub(r"\(\(.+?\)\)", "", text, flags=re.DOTALL)
    fiction = re.sub(r"[ \t]{2,}", " ", fiction).strip()
    return fiction, " ".join(p.strip() for p in ooc_parts)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _paragraphs(text: str) -> list[str]:
    return [p for p in text.split("\n\n") if p.strip()] or ([text] if text else [])
