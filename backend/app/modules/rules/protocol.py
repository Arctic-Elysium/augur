"""The ruleset protocol.

Everything upstream of the rules - the turn loop, the narrator, the memory
layer, the tool surface exposed to the model - depends only on this protocol
and on the normalized types in `types.py`. It never knows what dice a system
rolls.

The contract is deliberately narrow. Depth belongs *inside* an implementation,
not in the protocol; a system that wants faction standing, downtime actions, or
an eight-school magic system builds those behind `resolve_check` and
`apply_consequence` rather than widening the seam.

Adding a system: create a directory under `systems/`, implement `Ruleset`,
register it in `registry.py`. One line.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.modules.rules.dice import Rng
from app.modules.rules.types import (
    Character,
    CheckKind,
    CheckRequest,
    CheckResult,
    ConditionSpec,
    StateDelta,
)


@runtime_checkable
class Ruleset(Protocol):
    id: str
    name: str
    version: str

    # --- character ---

    def character_schema(self) -> dict[str, Any]:
        """JSON Schema for a sheet in this system. Drives the creation UI."""
        ...

    def create_character(self, spec: dict[str, Any]) -> Character: ...

    # --- checks ---

    def check_kinds(self) -> tuple[CheckKind, ...]:
        """Every check this system defines. The model picks from this list and
        may not invent new ones."""
        ...

    def total_modifier(self, actor: Character, kind_id: str, situational: int) -> int:
        """Attribute + skill + conditions + situational, summed."""
        ...

    def roll_required(self, actor: Character, request: CheckRequest) -> tuple[bool, str | None]:
        """Gate before any dice are thrown.

        Returns (should_roll, reason_if_not). This is what stops a nat 20 from
        letting anyone eventually do anything: a task far beyond the actor is
        an auto-failure and never rolled, and a trivial one is an auto-success
        so a nat 1 can't make a competent character look foolish at nothing.
        """
        ...

    def resolve_check(
        self, actor: Character, request: CheckRequest, rng: Rng
    ) -> CheckResult: ...

    # --- state ---

    def condition_specs(self) -> tuple[ConditionSpec, ...]: ...

    def apply_consequence(
        self, actor: Character, result: CheckResult
    ) -> StateDelta:
        """Mechanical fallout the system mandates from a result. Does not
        include anything the narrator invents."""
        ...

    def tick_conditions(self, actor: Character) -> StateDelta: ...

    def apply_delta(self, actor: Character, delta: StateDelta) -> Character:
        """Pure: returns a new Character. Never mutates."""
        ...

    # --- model-facing ---

    def describe_for_model(self, actor: Character) -> str:
        """How this sheet is explained to the GM model. Kept in the ruleset so
        each system controls its own vocabulary."""
        ...
