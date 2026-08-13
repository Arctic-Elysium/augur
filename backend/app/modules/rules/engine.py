"""The rules engine facade.

This is the only surface the turn loop and the AI tool layer touch. It composes
a ruleset with the check ledger so lock evaluation and resolution happen
together, and it is the single place a check can be resolved - which means it
is the single place to audit when something goes wrong.

Pure: no I/O, no database, no model calls. Callers apply the returned deltas.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.rules import registry
from app.modules.rules.dice import Rng, SeededRng
from app.modules.rules.locking import CheckLedger, Situation
from app.modules.rules.protocol import Ruleset
from app.modules.rules.types import (
    Character,
    CheckRequest,
    CheckResult,
    Clock,
    StateDelta,
)


@dataclass(frozen=True)
class Resolution:
    """A resolved check plus everything that follows from it mechanically."""

    result: CheckResult
    delta: StateDelta
    actor_after: Character
    locked: bool = False


class RulesEngine:
    def __init__(
        self,
        ruleset_id: str = "d20",
        ledger: CheckLedger | None = None,
        rng: Rng | None = None,
    ) -> None:
        self._ruleset: Ruleset = registry.get(ruleset_id)
        self._ledger = ledger or CheckLedger()
        self._rng = rng or SeededRng()

    @property
    def ruleset(self) -> Ruleset:
        return self._ruleset

    @property
    def ledger(self) -> CheckLedger:
        return self._ledger

    def resolve(
        self, actor: Character, request: CheckRequest, situation: Situation
    ) -> Resolution:
        kind = next(
            (k for k in self._ruleset.check_kinds() if k.id == request.kind_id), None
        )
        if kind is None:
            from app.core.errors import InvalidRequest

            raise InvalidRequest(f"unknown check kind: {request.kind_id}")

        cached = self._ledger.lookup(request, kind, situation)
        if cached is not None:
            return Resolution(cached, StateDelta(), actor, locked=True)

        result = self._ruleset.resolve_check(actor, request, self._rng)
        delta = self._ruleset.apply_consequence(actor, result)
        actor_after = self._ruleset.apply_delta(actor, delta)

        if result.rolled:
            self._ledger.record(request, kind, situation, result)

        return Resolution(result, delta, actor_after)

    def tick(self, actor: Character) -> Resolution:
        """End-of-round: conditions tick, durations decrement."""
        delta = self._ruleset.tick_conditions(actor)
        return Resolution(
            result=_NO_CHECK, delta=delta,
            actor_after=self._ruleset.apply_delta(actor, delta),
        )

    def advance_clock(self, clock: Clock, segments: int = 1) -> Clock:
        return clock.advance(segments)

    def describe(self, actor: Character) -> str:
        return self._ruleset.describe_for_model(actor)


from app.modules.rules.dice import roll_d20  # noqa: E402
from app.modules.rules.types import Tier  # noqa: E402


class _FixedRng:
    def randint(self, a: int, b: int) -> int:
        return a


_NO_CHECK = CheckResult(
    tier=Tier.SUCCESS,
    margin=0,
    natural=0,
    dc=0,
    roll=roll_d20(_FixedRng()),
    reason="no check",
)
