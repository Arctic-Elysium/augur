"""Memory: the entity store, canon, and the context source.

This replaces `InMemoryContextSource` behind the interface Milestone 2 fixed,
so the turn loop needed no changes to gain a real memory.

The load-bearing property is unchanged: **context size is bounded and does not
grow with campaign length.** Retrieval is ranked and capped here; the
`ContextBuilder` enforces token budgets on top.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.memory.models import (
    EntryStatus,
    CanonFact,
    Entity,
    EntityKind,
    Note,
    Summary,
    SummaryLevel,
)
from app.platform.ai.context import CanonFact as CanonFactView
from app.platform.ai.context import EntityBrief, Exchange
from app.platform.ai.context import Summary as SummaryView

# Retrieval caps. The token budget is the real limit, but capping row counts
# first keeps a campaign with 3000 entities from serialising all of them only
# for the builder to throw most away.
MAX_ENTITIES = 24
MAX_FACTS = 40
MAX_SUMMARIES = 60


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:100] or "unnamed"


def make_ref(kind: str, name: str) -> str:
    return f"{kind}:{slugify(name)}"


class MemoryService:
    def __init__(self, db: AsyncSession, campaign_id: uuid.UUID) -> None:
        self._db = db
        self._campaign_id = campaign_id

    # ------------------------------------------------------------ entities

    async def upsert_entity(
        self,
        *,
        kind: str,
        name: str,
        summary: str = "",
        state: dict | None = None,
        session_number: int | None = None,
        known_to_players: bool = True,
        status: EntryStatus = EntryStatus.PROPOSED,
    ) -> Entity:
        """Create or refresh. Idempotent on ref, so the same NPC mentioned in
        five scenes is one row with five mentions rather than five rows.

        Extraction calls this with the default PROPOSED status: nothing the
        model writes reaches canon until a human accepts it. Re-mentioning an
        already-accepted entity must never demote it back to the queue, so
        status only ever moves forward here.
        """
        try:
            entity_kind = EntityKind(kind)
        except ValueError:
            entity_kind = EntityKind.CONCEPT

        ref = make_ref(entity_kind.value, name)
        existing = await self._db.execute(
            select(Entity).where(
                Entity.campaign_id == self._campaign_id, Entity.ref == ref
            )
        )
        entity = existing.scalar_one_or_none()

        # Exact ref missed, so try to fold in a near-duplicate before creating
        # a second row. Extraction names the same person slightly differently
        # from one scene to the next, and two rows for one guard means two
        # histories, two sets of facts, and a codex that looks broken.
        if entity is None:
            entity = await self._find_similar(entity_kind, name)

        if entity is None:
            entity = Entity(
                campaign_id=self._campaign_id,
                ref=ref,
                kind=entity_kind,
                name=name,
                summary=summary,
                state=state or {},
                first_seen_session=session_number,
                known_to_players=known_to_players,
                status=status,
                proposed_in_session=session_number,
            )
            self._db.add(entity)
        else:
            entity.mentions += 1
            # Only widen. A later mention that says nothing new must not blank
            # out a summary written when the entity was first established.
            if summary and len(summary) > len(entity.summary):
                entity.summary = summary
            if state:
                entity.state = {**entity.state, **state}
            if known_to_players:
                entity.known_to_players = True
            # Forward only. An accepted entity being mentioned again is not a
            # new proposal, and dropping it back into the queue every time it
            # recurs would make the queue unusable within one session.
            if status is EntryStatus.ACCEPTED:
                entity.status = EntryStatus.ACCEPTED

        await self._db.flush()
        return entity

    async def _find_similar(self, kind: EntityKind, name: str) -> Entity | None:
        """Match on the distinctive part of a name, within a kind.

        "the guard" and "cart guard" share `guard`; "Serel" and "Serel the
        innkeeper" share `serel`. Deliberately conservative - merging two
        genuinely different people is worse than leaving a duplicate, because a
        merge cannot be undone from the UI.
        """
        target = slugify(name)
        stop = {"the", "a", "an", "of", "and"}
        words = [w for w in target.split("-") if w and w not in stop]
        if not words:
            return None

        result = await self._db.execute(
            select(Entity).where(
                Entity.campaign_id == self._campaign_id, Entity.kind == kind
            )
        )
        for candidate in result.scalars():
            body = candidate.ref.split(":", 1)[-1]
            other = [w for w in body.split("-") if w and w not in stop]
            if not other:
                continue
            # One is a subsequence of the other, or they share a rare head word.
            if set(words) <= set(other) or set(other) <= set(words):
                return candidate
        return None

    async def entities(
        self,
        *,
        known_only: bool = False,
        kinds: list[str] | None = None,
        status: EntryStatus | None = EntryStatus.ACCEPTED,
    ) -> list[Entity]:
        """Accepted entries only, by default.

        The default matters more than it looks: this is what the context
        source and the extractor's roster both call, so a proposed entry is
        invisible to the model until a human accepts it. Pass status=None to
        see everything, which is what the review queue does.
        """
        query = select(Entity).where(Entity.campaign_id == self._campaign_id)
        if known_only:
            query = query.where(Entity.known_to_players.is_(True))
        if status is not None:
            query = query.where(Entity.status == status)
        if kinds:
            query = query.where(Entity.kind.in_([EntityKind(k) for k in kinds]))
        result = await self._db.execute(
            query.order_by(desc(Entity.mentions), Entity.name)
        )
        return list(result.scalars())

    async def entity_by_ref(self, ref: str) -> Entity | None:
        result = await self._db.execute(
            select(Entity).where(
                Entity.campaign_id == self._campaign_id, Entity.ref == ref
            )
        )
        return result.scalar_one_or_none()

    async def resolve_target(self, text: str, kind_hint: str = "") -> str:
        """Map free text to a stable ref.

        This is what closes the check-locking gap from Milestone 1: "search the
        desk" and "look through the drawers" must resolve to the same ref, or a
        player launders a retry by rewording. Exact match first, then a
        contains match against known names.
        """
        candidate = make_ref(kind_hint or "concept", text)
        if await self.entity_by_ref(candidate):
            return candidate

        needle = slugify(text)
        result = await self._db.execute(
            select(Entity).where(Entity.campaign_id == self._campaign_id)
        )
        for entity in result.scalars():
            body = entity.ref.split(":", 1)[-1]
            if needle == body or needle in body or body in needle:
                return entity.ref
        return candidate

    # ------------------------------------------------------------ canon

    async def add_fact(
        self,
        *,
        subject_ref: str,
        predicate: str,
        object_text: str,
        session_number: int | None = None,
        turn_id: uuid.UUID | None = None,
        secret: bool = False,
        status: EntryStatus = EntryStatus.PROPOSED,
    ) -> CanonFact | None:
        """Record a claim. Returns None if it is already on record."""
        duplicate = await self._db.execute(
            select(CanonFact).where(
                CanonFact.campaign_id == self._campaign_id,
                CanonFact.subject_ref == subject_ref,
                CanonFact.predicate == predicate,
                CanonFact.object_text == object_text,
                CanonFact.retracted.is_(False),
            )
        )
        if duplicate.scalar_one_or_none() is not None:
            return None

        fact = CanonFact(
            campaign_id=self._campaign_id,
            subject_ref=subject_ref,
            predicate=predicate,
            object_text=object_text,
            session_number=session_number,
            source_turn_id=turn_id,
            secret=secret,
            status=status,
        )
        self._db.add(fact)
        await self._db.flush()
        return fact

    async def facts(
        self,
        *,
        subject_refs: list[str] | None = None,
        include_secret: bool = True,
        status: EntryStatus | None = EntryStatus.ACCEPTED,
        include_superseded: bool = False,
    ) -> list[CanonFact]:
        """Current, accepted facts by default.

        Superseded facts are excluded from the default read because they are
        no longer true - but they are not excluded from the *world*. The
        context source asks for them separately and renders them as history,
        which is how the model knows Borveld used to run an inn.
        """
        query = select(CanonFact).where(
            CanonFact.campaign_id == self._campaign_id,
            CanonFact.retracted.is_(False),
        )
        if status is not None:
            query = query.where(CanonFact.status == status)
        if not include_superseded:
            query = query.where(CanonFact.superseded_by_id.is_(None))
        if subject_refs:
            query = query.where(CanonFact.subject_ref.in_(subject_refs))
        if not include_secret:
            query = query.where(CanonFact.secret.is_(False))
        result = await self._db.execute(query.order_by(desc(CanonFact.created_at)))
        return list(result.scalars())

    async def retract(self, fact_id: uuid.UUID) -> None:
        """This was never true. Mis-extraction, or a mistake being corrected.

        Soft-deleted rather than removed so the row can still be traced back
        to the turn that produced it, but the model never sees it again.
        """
        fact = await self._db.get(CanonFact, fact_id)
        if fact is not None:
            fact.retracted = True
            await self._db.flush()

    async def supersede(
        self,
        fact_id: uuid.UUID,
        *,
        predicate: str | None = None,
        object_text: str,
        session_number: int | None = None,
        secret: bool | None = None,
    ) -> CanonFact | None:
        """This WAS true and now is not. The distinction from retraction is
        the whole point: Borveld really did run that inn, and the fact that he
        now does not is a thing that *happened* rather than a thing that was
        wrong. The old fact stays, marked, and is rendered to the model as
        history so the townspeople can remember the inn.
        """
        old = await self._db.get(CanonFact, fact_id)
        if old is None or old.campaign_id != self._campaign_id:
            return None

        replacement = CanonFact(
            campaign_id=self._campaign_id,
            subject_ref=old.subject_ref,
            predicate=predicate or old.predicate,
            object_text=object_text,
            session_number=session_number,
            secret=old.secret if secret is None else secret,
            # A human is performing this edit, so it is canon immediately.
            status=EntryStatus.ACCEPTED,
        )
        self._db.add(replacement)
        await self._db.flush()

        old.superseded_by_id = replacement.id
        old.superseded_at_session = session_number
        await self._db.flush()
        return replacement

    async def pending(self, *, session_number: int | None = None) -> dict:
        """The review queue: everything the model proposed and no human has
        ruled on yet."""
        entity_q = select(Entity).where(
            Entity.campaign_id == self._campaign_id,
            Entity.status == EntryStatus.PROPOSED,
        )
        fact_q = select(CanonFact).where(
            CanonFact.campaign_id == self._campaign_id,
            CanonFact.status == EntryStatus.PROPOSED,
            CanonFact.retracted.is_(False),
        )
        if session_number is not None:
            entity_q = entity_q.where(Entity.proposed_in_session == session_number)
            fact_q = fact_q.where(CanonFact.session_number == session_number)
        entities = list(
            (await self._db.execute(entity_q.order_by(desc(Entity.mentions)))).scalars()
        )
        facts = list(
            (await self._db.execute(fact_q.order_by(desc(CanonFact.created_at)))).scalars()
        )
        return {"entities": entities, "facts": facts}

    async def transform_entity(
        self,
        ref: str,
        *,
        summary: str,
        note: str = "",
        session_number: int | None = None,
    ) -> Entity | None:
        """Change what an entry says while keeping what it used to say.

        The counterpart to editing in place. Correcting a wrong entry should
        leave no trace; recording that the world changed should leave nothing
        but. Same gesture in the UI, opposite treatment in context.
        """
        entity = await self.entity_by_ref(ref)
        if entity is None:
            return None
        entity.history = [
            *entity.history,
            {
                "session": session_number,
                "summary": entity.summary,
                "note": note,
            },
        ]
        entity.summary = summary
        await self._db.flush()
        return entity

    # ------------------------------------------------------------ seeding

    async def seed_from_character(
        self, name: str, backstory: str | None, hooks: list[dict]
    ) -> None:
        """Turn a character's threads into canon.

        Without this the hooks are inert - a player writes "I owe Karal for the
        boat" at creation and the GM never sees it again. Seeded as facts, they
        are injected whenever that character is in play.
        """
        ref = make_ref("npc", name)
        await self.upsert_entity(kind="npc", name=name, summary=(backstory or "")[:600])

        for hook in hooks:
            subject = (hook.get("subject") or "").strip()
            if not subject:
                continue
            detail = (hook.get("detail") or "").strip()
            await self.add_fact(
                subject_ref=ref,
                predicate=f"has a {hook.get('kind', 'thread')} concerning",
                object_text=f"{subject}{f' — {detail}' if detail else ''}",
            )
            # The subject of a bond or debt is usually a person or place worth
            # tracking in its own right.
            if hook.get("kind") in ("bond", "debt"):
                await self.upsert_entity(
                    kind="npc", name=subject, summary=detail[:400]
                )

    # ------------------------------------------------------------ summaries

    async def add_summary(
        self,
        *,
        level: SummaryLevel,
        body: str,
        title: str = "",
        session_number: int | None = None,
        entity_refs: list[str] | None = None,
    ) -> Summary:
        summary = Summary(
            campaign_id=self._campaign_id,
            level=int(level),
            title=title,
            body=body,
            session_number=session_number,
            entity_refs=entity_refs or [],
        )
        self._db.add(summary)
        await self._db.flush()
        return summary

    async def summaries(self) -> list[Summary]:
        result = await self._db.execute(
            select(Summary)
            .where(Summary.campaign_id == self._campaign_id)
            .order_by(desc(Summary.level), desc(Summary.created_at))
            .limit(MAX_SUMMARIES)
        )
        return list(result.scalars())

    # ------------------------------------------------------------ notes

    async def notes(self, author_id: uuid.UUID) -> list[Note]:
        result = await self._db.execute(
            select(Note)
            .where(Note.campaign_id == self._campaign_id, Note.author_id == author_id)
            .order_by(desc(Note.pinned), desc(Note.updated_at))
        )
        return list(result.scalars())

    async def entity_count(self) -> int:
        return await self._db.scalar(
            select(func.count()).select_from(Entity).where(
                Entity.campaign_id == self._campaign_id
            )
        ) or 0


# Deliberately NOT subclassing ContextSource. It is a Protocol with `...`
# bodies, so inheriting it means any signature mismatch silently falls through
# to a stub returning None instead of failing loudly. Structural typing gives
# the same guarantee without the trap.
class DatabaseContextSource:
    """The real context source. Same interface the fake one had.

    Retrieval is ranked, not exhaustive: entities by mention count, facts about
    entities actually in play, summaries preferring higher rungs. The builder
    then enforces the token budget per layer.
    """

    def __init__(
        self,
        memory: MemoryService,
        *,
        in_play_refs: list[str] | None = None,
        recent: list[Exchange] | None = None,
        previous: list[Exchange] | None = None,
    ) -> None:
        self._memory = memory
        self._in_play = in_play_refs or []
        self._recent = recent or []
        self._previous = previous or []
        self._cache: dict[str, object] = {}

    async def preload(self) -> None:
        """One round of queries up front.

        The ContextSource interface is synchronous because the builder is; the
        async work happens here and the accessors read the cache.
        """
        # Accepted entries only. A proposal sitting in the review queue is
        # not yet part of the world, and feeding it to the model would defeat
        # the point of the queue - the model would build on material the GM is
        # about to throw away.
        entities = await self._memory.entities()
        # In-play first, then whatever comes up most often.
        in_play = [e for e in entities if e.ref in self._in_play]
        rest = [e for e in entities if e.ref not in self._in_play]
        selected = (in_play + rest)[:MAX_ENTITIES]

        refs = [e.ref for e in selected]
        facts = await self._memory.facts(subject_refs=refs or None)
        # Superseded facts are no longer true, and the model still needs them:
        # the world remembers that Borveld ran an inn before he died. Rendered
        # separately as history so they cannot be mistaken for current canon.
        history = await self._memory.facts(
            subject_refs=refs or None,
            include_superseded=True,
        )

        self._cache["entities"] = [
            EntityBrief(
                id=e.ref,
                kind=e.kind.value,
                name=e.name,
                summary=e.summary,
                disposition=str(e.state.get("disposition") or "") or None,
                # What this entry used to say, most recent first.
                was=[str(h.get("summary") or "") for h in reversed(e.history)][:2],
            )
            for e in selected
        ]
        self._cache["canon"] = [
            CanonFactView(
                subject=f.subject_ref, predicate=f.predicate, object=f.object_text
            )
            for f in facts[:MAX_FACTS]
        ]
        self._cache["superseded"] = [
            CanonFactView(
                subject=f.subject_ref,
                predicate=f.predicate,
                object=f.object_text,
                superseded_at=f.superseded_at_session,
            )
            for f in history
            if f.superseded_by_id is not None
        ][:MAX_FACTS]
        self._cache["summaries"] = [
            SummaryView(level=s.level, label=s.title, text=s.body)
            for s in await self._memory.summaries()
        ]

    def superseded_facts(self, session_id: str) -> list[CanonFactView]:
        return self._cache.get("superseded", [])  # type: ignore[return-value]

    def canon_for_scene(
        self, session_id: str, scene_id: str
    ) -> list[CanonFactView]:
        return self._cache.get("canon", [])  # type: ignore[return-value]

    def entities_in_play(
        self, session_id: str, scene_id: str
    ) -> list[EntityBrief]:
        return self._cache.get("entities", [])  # type: ignore[return-value]

    def summary_ladder(self, session_id: str) -> list[SummaryView]:
        return self._cache.get("summaries", [])  # type: ignore[return-value]

    def previous_session(self, session_id: str) -> list[Exchange]:
        return self._previous

    def recent_exchanges(self, session_id: str, limit: int) -> list[Exchange]:
        return self._recent[-limit:]
