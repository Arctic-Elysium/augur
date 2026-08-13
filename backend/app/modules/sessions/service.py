"""Session lifecycle and turn persistence.

The database is the source of truth for everything durable. In-memory state
exists only for the duration of a turn, so a pod restart between turns costs
nothing but a reconnect - which matters because sessions eventually move into
ephemeral per-session pods.
"""

from __future__ import annotations

import secrets
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.modules.characters.models import Character as CharacterRow
from app.modules.characters.models import Controller
from app.modules.rules import registry
from app.modules.rules.dice import SeededRng
from app.modules.rules.engine import RulesEngine
from app.modules.rules.locking import CheckLedger
from app.modules.rules.types import Character as RulesCharacter
from app.modules.rules.types import Clock
from app.modules.sessions.models import (
    CheckLock,
    PlaySession,
    SessionClock,
    SessionStatus,
    Turn,
)
from app.platform.ai.context import ContextPacket, Exchange


class SessionService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ------------------------------------------------------------ lifecycle

    async def start(
        self, campaign_id: uuid.UUID, *, ruleset_id: str = "d20"
    ) -> PlaySession:
        existing = await self._db.execute(
            select(PlaySession).where(
                PlaySession.campaign_id == campaign_id,
                PlaySession.status == SessionStatus.ACTIVE,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise ConflictError(
                "this campaign already has an active session",
                detail={"hint": "end it before starting another"},
            )

        count = await self._db.scalar(
            select(func.count()).select_from(PlaySession).where(
                PlaySession.campaign_id == campaign_id
            )
        )
        session = PlaySession(
            campaign_id=campaign_id,
            number=(count or 0) + 1,
            # Recorded so every roll in the session can be replayed exactly.
            seed=secrets.randbits(31),
        )
        self._db.add(session)
        await self._db.flush()
        return session

    async def get(self, session_id: uuid.UUID) -> PlaySession:
        session = await self._db.get(PlaySession, session_id)
        if session is None:
            raise NotFoundError("session not found")
        return session

    async def active_for_campaign(self, campaign_id: uuid.UUID) -> PlaySession | None:
        result = await self._db.execute(
            select(PlaySession).where(
                PlaySession.campaign_id == campaign_id,
                PlaySession.status == SessionStatus.ACTIVE,
            )
        )
        return result.scalar_one_or_none()

    async def end(self, session_id: uuid.UUID, summary: str = "") -> PlaySession:
        session = await self.get(session_id)
        session.status = SessionStatus.ENDED
        session.summary = summary or session.summary
        session.ended_at = func.now()
        await self._db.flush()
        return session

    async def set_spotlight(
        self, session_id: uuid.UUID, character_id: uuid.UUID | None
    ) -> PlaySession:
        """Null means the party acts together."""
        session = await self.get(session_id)
        session.active_character_id = character_id
        await self._db.flush()
        return session

    # ------------------------------------------------------------ party

    async def party(self, campaign_id: uuid.UUID) -> dict[str, RulesCharacter]:
        """The active roster, as rules-engine characters keyed by id string."""
        result = await self._db.execute(
            select(CharacterRow).where(
                CharacterRow.campaign_id == campaign_id,
                CharacterRow.active.is_(True),
            ).order_by(CharacterRow.created_at)
        )
        return {str(row.id): self._to_rules(row) for row in result.scalars()}

    @staticmethod
    def _to_rules(row: CharacterRow) -> RulesCharacter:
        from app.modules.rules.types import ActiveCondition

        sheet = row.sheet or {}
        return RulesCharacter(
            id=str(row.id),
            name=row.name,
            attributes=sheet.get("attributes", {}),
            skills=sheet.get("skills", {}),
            hp=sheet.get("hp", 10),
            hp_max=sheet.get("hp_max", 10),
            stress=sheet.get("stress", 0),
            stress_max=sheet.get("stress_max", 6),
            conditions=tuple(
                ActiveCondition(**c) for c in sheet.get("conditions", [])
            ),
            inventory=tuple(sheet.get("inventory", [])),
            level=sheet.get("level", 1),
        )

    @staticmethod
    def _to_sheet(character: RulesCharacter) -> dict:
        return {
            "attributes": character.attributes,
            "skills": character.skills,
            "hp": character.hp,
            "hp_max": character.hp_max,
            "stress": character.stress,
            "stress_max": character.stress_max,
            "conditions": [
                {"spec_id": c.spec_id, "remaining_ticks": c.remaining_ticks,
                 "source": c.source}
                for c in character.conditions
            ],
            "inventory": list(character.inventory),
            "level": character.level,
        }

    async def save_party(self, party: dict[str, RulesCharacter]) -> None:
        for character_id, character in party.items():
            row = await self._db.get(CharacterRow, uuid.UUID(character_id))
            if row is not None:
                row.sheet = self._to_sheet(character)
        await self._db.flush()

    # ------------------------------------------------------------ clocks

    async def clocks(self, campaign_id: uuid.UUID) -> dict[str, Clock]:
        result = await self._db.execute(
            select(SessionClock).where(SessionClock.campaign_id == campaign_id)
        )
        return {
            row.clock_id: Clock(row.clock_id, row.label, row.size, row.filled, row.hidden)
            for row in result.scalars()
        }

    async def save_clocks(
        self, campaign_id: uuid.UUID, clocks: dict[str, Clock]
    ) -> None:
        existing = await self._db.execute(
            select(SessionClock).where(SessionClock.campaign_id == campaign_id)
        )
        by_id = {row.clock_id: row for row in existing.scalars()}
        for clock_id, clock in clocks.items():
            row = by_id.get(clock_id)
            if row is None:
                self._db.add(SessionClock(
                    campaign_id=campaign_id, clock_id=clock.id, label=clock.label,
                    size=clock.size, filled=clock.filled, hidden=clock.hidden,
                ))
            else:
                row.filled = clock.filled
                row.label = clock.label
        await self._db.flush()

    # ------------------------------------------------------------ turns

    async def next_ordinal(self, session_id: uuid.UUID) -> int:
        highest = await self._db.scalar(
            select(func.max(Turn.ordinal)).where(Turn.session_id == session_id)
        )
        return (highest or 0) + 1

    async def record_turn(
        self,
        session: PlaySession,
        *,
        actor_id: uuid.UUID | None,
        player_input: str,
        narration: str,
        tool_calls: list,
        deltas: list,
        prompt_version: str,
    ) -> Turn:
        turn = Turn(
            session_id=session.id,
            ordinal=await self.next_ordinal(session.id),
            actor_id=actor_id,
            player_input=player_input,
            narration=narration,
            tool_calls=tool_calls,
            deltas=deltas,
            scene_id=session.scene_id,
            prompt_version=prompt_version,
        )
        self._db.add(turn)
        await self._db.flush()
        return turn

    async def recent_exchanges(
        self, session_id: uuid.UUID, limit: int = 12
    ) -> list[Exchange]:
        result = await self._db.execute(
            select(Turn).where(Turn.session_id == session_id)
            .order_by(Turn.ordinal.desc()).limit(limit)
        )
        turns = list(reversed(list(result.scalars())))
        exchanges: list[Exchange] = []
        for turn in turns:
            exchanges.append(Exchange("Player", turn.player_input))
            if turn.narration:
                exchanges.append(Exchange("GM", turn.narration))
        return exchanges

    async def previous_session_exchanges(
        self, campaign_id: uuid.UUID, before_number: int
    ) -> list[Exchange]:
        """The prior session in full. Continuity needs verbatim, not summary."""
        result = await self._db.execute(
            select(PlaySession).where(
                PlaySession.campaign_id == campaign_id,
                PlaySession.number < before_number,
            ).order_by(PlaySession.number.desc()).limit(1)
        )
        prior = result.scalar_one_or_none()
        if prior is None:
            return []
        # Capped, and only the tail. Once a session has a summary the ladder
        # carries its substance; what the verbatim tail adds is continuity with
        # the moment you stopped, which is the last few exchanges, not all of
        # them. Uncapped, this grew with how long you played last time.
        return await self.recent_exchanges(prior.id, limit=40)

    # ------------------------------------------------------------ engine

    async def engine_for(
        self, session: PlaySession, ruleset_id: str = "d20"
    ) -> RulesEngine:
        """A rules engine seeded from the session and preloaded with its locks.

        Locks are hydrated from the database because an in-memory ledger would
        reset on pod restart, quietly handing the player a fresh attempt at
        everything they already failed.
        """
        ledger = CheckLedger()
        result = await self._db.execute(
            select(CheckLock).where(CheckLock.campaign_id == session.campaign_id)
        )
        for row in result.scalars():
            ledger.restore(
                actor_id=row.actor_ref, kind_id=row.kind_id,
                target_ref=row.target_ref, fingerprint=row.fingerprint,
                scene_id=row.scene_id, result=row.result,
            )
        return RulesEngine(ruleset_id, ledger=ledger, rng=SeededRng(session.seed))

    async def persist_locks(
        self, campaign_id: uuid.UUID, ledger: CheckLedger
    ) -> None:
        existing = await self._db.execute(
            select(CheckLock).where(CheckLock.campaign_id == campaign_id)
        )
        known = {
            (r.actor_ref, r.kind_id, r.target_ref, r.fingerprint)
            for r in existing.scalars()
        }
        for entry in ledger.entries():
            key = (entry.key.actor_id, entry.key.kind_id,
                   entry.key.target_ref, entry.key.fingerprint)
            if key in known:
                continue
            self._db.add(CheckLock(
                campaign_id=campaign_id, actor_ref=entry.key.actor_id,
                kind_id=entry.key.kind_id, target_ref=entry.key.target_ref,
                fingerprint=entry.key.fingerprint, scene_id=entry.scene_id,
                result=entry.serialized,
            ))
        await self._db.flush()
