"""Core rules types.

Everything here is data. No I/O, no database, no model calls. These types are
the vocabulary the narrator and the AI gateway speak; a ruleset implementation
decides how to produce them.

The important invariant: a `CheckResult` is fully self-describing. A narrator
looking at one knows the tier, the magnitude, whether a natural die forced the
outcome, and exactly what boon or setback it owes the player - without knowing
what dice were rolled or which system produced it.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------- dice


@dataclass(frozen=True)
class DiceRoll:
    """One evaluated dice expression. `kept` is what actually counted."""

    notation: str
    rolls: tuple[int, ...]
    kept: tuple[int, ...]
    modifier: int
    total: int

    @property
    def natural(self) -> int:
        """The single die face that decides criticals.

        With advantage/disadvantage this is the *kept* die, never the discarded
        one - otherwise advantage silently doubles the natural-20 rate.
        """
        return self.kept[0] if len(self.kept) == 1 else max(self.kept)


class RollMode(str, enum.Enum):
    NORMAL = "normal"
    ADVANTAGE = "advantage"
    DISADVANTAGE = "disadvantage"


# ---------------------------------------------------------------- outcomes


class Tier(str, enum.Enum):
    """Five degrees of success, ordered worst to best."""

    CRIT_FAILURE = "crit_failure"
    FAILURE = "failure"
    PARTIAL = "partial"
    SUCCESS = "success"
    CRIT_SUCCESS = "crit_success"

    @property
    def rank(self) -> int:
        return _TIER_ORDER.index(self)

    @property
    def is_success(self) -> bool:
        return self in (Tier.PARTIAL, Tier.SUCCESS, Tier.CRIT_SUCCESS)


_TIER_ORDER = [
    Tier.CRIT_FAILURE,
    Tier.FAILURE,
    Tier.PARTIAL,
    Tier.SUCCESS,
    Tier.CRIT_SUCCESS,
]


def shift_tier(tier: Tier, steps: int) -> Tier:
    index = max(0, min(len(_TIER_ORDER) - 1, tier.rank + steps))
    return _TIER_ORDER[index]


class Scale(str, enum.Enum):
    MINOR = "minor"
    STANDARD = "standard"
    MAJOR = "major"


class BoonKind(str, enum.Enum):
    """Categories of 'and something extra' on a critical success.

    The engine picks the category and scale; the model invents the specific
    thing within those bounds. This is what stops a crit on a trivial search
    from producing a legendary artifact.
    """

    EXTRA_RESOURCE = "extra_resource"
    EXTRA_INFORMATION = "extra_information"
    POSITION_GAINED = "position_gained"
    CLOCK_REDUCED = "clock_reduced"
    CONDITION_CLEARED = "condition_cleared"
    DISPOSITION_IMPROVED = "disposition_improved"


class SetbackKind(str, enum.Enum):
    """Mirror of BoonKind for critical failures."""

    RESOURCE_LOST = "resource_lost"
    INFORMATION_LEAKED = "information_leaked"
    POSITION_LOST = "position_lost"
    CLOCK_ADVANCED = "clock_advanced"
    CONDITION_APPLIED = "condition_applied"
    DISPOSITION_DAMAGED = "disposition_damaged"


@dataclass(frozen=True)
class BoonSpec:
    kind: BoonKind
    scale: Scale
    # Free-text guidance for the narrator, e.g. "relates to what was searched".
    hint: str = ""


@dataclass(frozen=True)
class SetbackSpec:
    kind: SetbackKind
    scale: Scale
    hint: str = ""


@dataclass(frozen=True)
class CheckResult:
    """The normalized outcome every ruleset must produce."""

    tier: Tier
    margin: int
    natural: int
    dc: int
    roll: DiceRoll
    # True when a natural 20/1 forced the tier regardless of the arithmetic.
    override: bool = False
    boon: BoonSpec | None = None
    setback: SetbackSpec | None = None
    # Set when the check was not rolled: locked, auto-passed, or auto-failed.
    reason: str | None = None

    @property
    def rolled(self) -> bool:
        return self.reason is None

    def to_narrator(self) -> dict[str, Any]:
        """Compact view handed to the model. Deliberately excludes internals."""
        payload: dict[str, Any] = {
            "tier": self.tier.value,
            "margin": self.margin,
            "natural": self.natural,
            "override": self.override,
        }
        if self.boon:
            payload["boon"] = {
                "kind": self.boon.kind.value,
                "scale": self.boon.scale.value,
                "hint": self.boon.hint,
            }
        if self.setback:
            payload["setback"] = {
                "kind": self.setback.kind.value,
                "scale": self.setback.scale.value,
                "hint": self.setback.hint,
            }
        if self.reason:
            payload["not_rolled"] = self.reason
        return payload


# ---------------------------------------------------------------- checks


class LockPolicy(str, enum.Enum):
    """How aggressively a check kind resists being retried.

    Checks against a *static* thing lock. Checks against an *active* opponent
    do not - you can always swing again.
    """

    NEVER = "never"
    PER_CONDITION_CHANGE = "per_condition_change"
    PER_SCENE = "per_scene"
    ONCE = "once"


@dataclass(frozen=True)
class CheckKind:
    id: str
    label: str
    attribute: str
    lock_policy: LockPolicy = LockPolicy.PER_CONDITION_CHANGE
    # Time-sensitive checks reopen when in-fiction time passes.
    time_sensitive: bool = False


@dataclass(frozen=True)
class CheckRequest:
    actor_id: str
    kind_id: str
    dc: int
    # Stable entity reference, not free text. "search the desk" and "look
    # through the drawers" must resolve to the same target_ref or the lock
    # is trivially laundered by rephrasing.
    target_ref: str | None = None
    mode: RollMode = RollMode.NORMAL
    situational: int = 0
    # Set when the actor spends a resource to force a retry.
    pushed: bool = False


# ---------------------------------------------------------------- state


@dataclass(frozen=True)
class ConditionSpec:
    id: str
    label: str
    # Flat modifier applied to checks using these attributes. Empty = all.
    check_modifier: int = 0
    affects_attributes: tuple[str, ...] = ()
    blocks_actions: tuple[str, ...] = ()
    damage_per_tick: int = 0
    description: str = ""


@dataclass(frozen=True)
class ActiveCondition:
    spec_id: str
    # None means indefinite - cleared only by an explicit action.
    remaining_ticks: int | None = None
    source: str = ""

    def tick(self) -> ActiveCondition | None:
        if self.remaining_ticks is None:
            return self
        remaining = self.remaining_ticks - 1
        return None if remaining <= 0 else ActiveCondition(
            self.spec_id, remaining, self.source
        )


@dataclass(frozen=True)
class Clock:
    """Segmented progress tracker. The best tool for making a world feel like
    it moves whether or not the player acts on it."""

    id: str
    label: str
    size: int
    filled: int = 0
    hidden: bool = False

    def __post_init__(self) -> None:
        if self.size <= 0:
            raise ValueError("clock size must be positive")
        if not 0 <= self.filled <= self.size:
            raise ValueError("clock filled out of range")

    @property
    def complete(self) -> bool:
        return self.filled >= self.size

    def advance(self, segments: int = 1) -> Clock:
        return Clock(
            self.id,
            self.label,
            self.size,
            min(self.size, self.filled + segments),
            self.hidden,
        )

    def reduce(self, segments: int = 1) -> Clock:
        return Clock(
            self.id,
            self.label,
            self.size,
            max(0, self.filled - segments),
            self.hidden,
        )


@dataclass(frozen=True)
class Character:
    id: str
    name: str
    attributes: dict[str, int]
    skills: dict[str, int] = field(default_factory=dict)
    hp: int = 10
    hp_max: int = 10
    stress: int = 0
    stress_max: int = 6
    conditions: tuple[ActiveCondition, ...] = ()
    inventory: tuple[str, ...] = ()
    level: int = 1

    @property
    def defeated(self) -> bool:
        return self.hp <= 0

    def attribute_mod(self, attribute: str) -> int:
        """3-18 score maps to -4..+4, the familiar d20 curve."""
        return (self.attributes.get(attribute, 10) - 10) // 2


@dataclass(frozen=True)
class StateDelta:
    """The only way rules describe change. Callers apply it; rules never mutate.

    Keeping this as data rather than in-place mutation is what lets the turn
    loop validate, log, and reject a model-proposed action before anything
    touches the database.
    """

    hp: int = 0
    stress: int = 0
    add_conditions: tuple[ActiveCondition, ...] = ()
    remove_conditions: tuple[str, ...] = ()
    add_items: tuple[str, ...] = ()
    remove_items: tuple[str, ...] = ()
    clock_changes: tuple[tuple[str, int], ...] = ()
    notes: tuple[str, ...] = ()

    def merge(self, other: StateDelta) -> StateDelta:
        return StateDelta(
            hp=self.hp + other.hp,
            stress=self.stress + other.stress,
            add_conditions=self.add_conditions + other.add_conditions,
            remove_conditions=self.remove_conditions + other.remove_conditions,
            add_items=self.add_items + other.add_items,
            remove_items=self.remove_items + other.remove_items,
            clock_changes=self.clock_changes + other.clock_changes,
            notes=self.notes + other.notes,
        )
