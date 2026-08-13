"""The primary system: d20 roll-over with five degrees of success.

Familiar enough that any player - and the model, which has enormous exposure to
d20 conventions - can sit down and play. Extended in three ways that matter for
an AI game master:

1. A partial-success band. Near-misses are the most common roll outcome, and
   "you get it, but it costs something" gives a narrator far more to work with
   than a binary. This is where most of the session-to-session variety lives.

2. Natural 20s and 1s are absolute, and they owe a boon or setback. The engine
   picks the *category* and *scale*; the model invents the specific thing
   within those bounds. That guarantees the extra always shows up and caps how
   large it can be.

3. A roll gate. Absolute criticals mean a nat 20 always crits - so tasks far
   beyond the actor must never reach the dice, or patience defeats difficulty.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import InvalidRequest
from app.modules.rules.dice import Rng, roll_d20
from app.modules.rules.types import (
    ActiveCondition,
    BoonKind,
    BoonSpec,
    Character,
    CheckKind,
    CheckRequest,
    CheckResult,
    ConditionSpec,
    LockPolicy,
    Scale,
    SetbackKind,
    SetbackSpec,
    StateDelta,
    Tier,
)

ATTRIBUTES = ("might", "agility", "endurance", "wits", "insight", "presence")

ATTRIBUTE_BLURBS = {
    "might": "Force, grip, and the weight you can put behind a thing.",
    "agility": "Speed, balance, and hands that do what you tell them.",
    "endurance": "What you can absorb and keep going through. Sets your health.",
    "wits": "Recall, reasoning, and how fast you put things together.",
    "insight": "What you notice, and what you read in people.",
    "presence": "How much room you take up in a conversation.",
}

# Point buy. Costs rise at the top so a character cannot be three maxima and
# three dump stats - the curve is what makes the choice interesting.
POINT_BASE = 8
POINT_MIN = 8
POINT_MAX = 15
POINT_BUDGET = 27
POINT_COSTS = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9}

SKILL_POINTS = 6
SKILL_MAX = 3

# Margin thresholds, applied to (d20 + modifiers) - dc.
CRIT_SUCCESS_MARGIN = 10
SUCCESS_MARGIN = 0
PARTIAL_MARGIN = -5      # -1..-4 is partial
CRIT_FAILURE_MARGIN = -10

# Roll gate. Beyond these spreads the outcome is not in doubt.
AUTO_FAIL_SPREAD = 20
AUTO_PASS_SPREAD = -10

CHECK_KINDS: tuple[CheckKind, ...] = (
    # Static targets lock - you do not get to search the same desk twice.
    CheckKind("search", "Search", "insight", LockPolicy.PER_CONDITION_CHANGE),
    CheckKind("recall", "Recall Lore", "wits", LockPolicy.ONCE),
    CheckKind("pick_lock", "Pick Lock", "agility", LockPolicy.PER_CONDITION_CHANGE),
    CheckKind("disarm_trap", "Disarm Trap", "agility", LockPolicy.PER_CONDITION_CHANGE),
    CheckKind("persuade", "Persuade", "presence", LockPolicy.PER_SCENE),
    CheckKind("deceive", "Deceive", "presence", LockPolicy.PER_SCENE),
    CheckKind("intimidate", "Intimidate", "presence", LockPolicy.PER_SCENE),
    CheckKind("decipher", "Decipher", "wits", LockPolicy.ONCE),
    # Active or repeatable - you can always swing again.
    CheckKind("strike", "Strike", "might", LockPolicy.NEVER),
    CheckKind("shoot", "Shoot", "agility", LockPolicy.NEVER),
    CheckKind("dodge", "Dodge", "agility", LockPolicy.NEVER),
    CheckKind("endure", "Endure", "endurance", LockPolicy.NEVER),
    CheckKind("sneak", "Sneak", "agility", LockPolicy.NEVER),
    CheckKind("climb", "Climb", "might", LockPolicy.NEVER, time_sensitive=True),
    CheckKind("perceive", "Perceive", "insight", LockPolicy.PER_SCENE),
)

_KINDS_BY_ID = {k.id: k for k in CHECK_KINDS}

CONDITIONS: tuple[ConditionSpec, ...] = (
    ConditionSpec("bleeding", "Bleeding", damage_per_tick=1,
                  description="Losing blood. Ticks damage each round."),
    ConditionSpec("poisoned", "Poisoned", check_modifier=-2, damage_per_tick=1,
                  description="Sickened and weakened."),
    ConditionSpec("frightened", "Frightened", check_modifier=-2,
                  affects_attributes=("presence", "wits"),
                  description="Fear clouds judgement and nerve."),
    ConditionSpec("prone", "Prone", check_modifier=-2,
                  affects_attributes=("might", "agility"),
                  blocks_actions=("climb",),
                  description="On the ground."),
    ConditionSpec("restrained", "Restrained", check_modifier=-4,
                  affects_attributes=("might", "agility"),
                  blocks_actions=("sneak", "climb", "dodge"),
                  description="Bound or held fast."),
    ConditionSpec("exhausted", "Exhausted", check_modifier=-2,
                  description="Running on empty."),
    ConditionSpec("blinded", "Blinded", check_modifier=-4,
                  affects_attributes=("insight", "agility"),
                  blocks_actions=("shoot", "search"),
                  description="Cannot see."),
    ConditionSpec("inspired", "Inspired", check_modifier=2,
                  description="Buoyed. A rare positive condition."),
)

_CONDITIONS_BY_ID = {c.id: c for c in CONDITIONS}


def _scale_for_dc(dc: int) -> Scale:
    """Critting a trivial check must not hand out a relic."""
    if dc <= 10:
        return Scale.MINOR
    if dc <= 18:
        return Scale.STANDARD
    return Scale.MAJOR


# Which flavour of "and something extra" fits which check.
_BOON_BY_KIND: dict[str, BoonKind] = {
    "search": BoonKind.EXTRA_RESOURCE,
    "perceive": BoonKind.EXTRA_INFORMATION,
    "recall": BoonKind.EXTRA_INFORMATION,
    "decipher": BoonKind.EXTRA_INFORMATION,
    "persuade": BoonKind.DISPOSITION_IMPROVED,
    "deceive": BoonKind.DISPOSITION_IMPROVED,
    "intimidate": BoonKind.DISPOSITION_IMPROVED,
    "pick_lock": BoonKind.POSITION_GAINED,
    "disarm_trap": BoonKind.CLOCK_REDUCED,
    "sneak": BoonKind.POSITION_GAINED,
    "endure": BoonKind.CONDITION_CLEARED,
}

_SETBACK_BY_KIND: dict[str, SetbackKind] = {
    "search": SetbackKind.CLOCK_ADVANCED,
    "perceive": SetbackKind.INFORMATION_LEAKED,
    "recall": SetbackKind.INFORMATION_LEAKED,
    "decipher": SetbackKind.INFORMATION_LEAKED,
    "persuade": SetbackKind.DISPOSITION_DAMAGED,
    "deceive": SetbackKind.DISPOSITION_DAMAGED,
    "intimidate": SetbackKind.DISPOSITION_DAMAGED,
    "pick_lock": SetbackKind.RESOURCE_LOST,
    "disarm_trap": SetbackKind.CONDITION_APPLIED,
    "sneak": SetbackKind.POSITION_LOST,
    "strike": SetbackKind.POSITION_LOST,
    "shoot": SetbackKind.RESOURCE_LOST,
    "climb": SetbackKind.CONDITION_APPLIED,
}


class D20Ruleset:
    id = "d20"
    name = "Augur d20"
    version = "1.0.0"

    # --- character ---

    def character_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["name", "attributes"],
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 80},
                "attributes": {
                    "type": "object",
                    "required": list(ATTRIBUTES),
                    "properties": {
                        a: {"type": "integer", "minimum": 3, "maximum": 18}
                        for a in ATTRIBUTES
                    },
                },
                "skills": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "integer", "minimum": 0, "maximum": 6
                    },
                },
                "hp_max": {"type": "integer", "minimum": 1},
            },
        }

    def build_rules(self) -> dict[str, Any]:
        return {
            "method": "point_buy",
            "attributes": [
                {"id": a, "label": a.capitalize(), "description": ATTRIBUTE_BLURBS[a]}
                for a in ATTRIBUTES
            ],
            "budget": POINT_BUDGET,
            "base": POINT_BASE,
            "min": POINT_MIN,
            "max": POINT_MAX,
            # Cumulative cost to reach each score from the base. Rising costs
            # at the top end are what stop a character being three 15s and
            # three dump stats.
            "costs": POINT_COSTS,
            "skills": [
                {"id": k.id, "label": k.label, "attribute": k.attribute}
                for k in CHECK_KINDS
            ],
            "skill_points": SKILL_POINTS,
            "skill_max": SKILL_MAX,
            "derived": {
                "hp_max": "10 + (endurance modifier x 2)",
                "stress_max": "6",
            },
        }

    def create_character(self, spec: dict[str, Any]) -> Character:
        attributes = spec.get("attributes") or {}
        missing = [a for a in ATTRIBUTES if a not in attributes]
        if missing:
            raise InvalidRequest(f"missing attributes: {', '.join(missing)}")
        for name, value in attributes.items():
            if name not in ATTRIBUTES:
                raise InvalidRequest(f"unknown attribute: {name}")
            if not 3 <= value <= 18:
                raise InvalidRequest(f"{name} out of range: {value}")

        # Enforce the point budget server-side. The builder shows a running
        # total, but a client is not a validator - nothing stops a crafted
        # request asking for six 18s.
        if spec.get("enforce_build", True):
            over = [
                n for n, v in attributes.items() if not POINT_MIN <= v <= POINT_MAX
            ]
            if over:
                raise InvalidRequest(
                    f"scores must be {POINT_MIN}-{POINT_MAX} at creation: "
                    f"{', '.join(over)}"
                )
            spent = sum(POINT_COSTS[v] for v in attributes.values())
            if spent > POINT_BUDGET:
                raise InvalidRequest(
                    f"point buy over budget: {spent} spent of {POINT_BUDGET}"
                )

        endurance_mod = (attributes["endurance"] - 10) // 2
        hp_max = spec.get("hp_max") or max(1, 10 + endurance_mod * 2)

        skills = spec.get("skills") or {}
        for skill, rank in skills.items():
            if skill not in _KINDS_BY_ID:
                raise InvalidRequest(f"unknown skill: {skill}")
            if not 0 <= rank <= SKILL_MAX:
                raise InvalidRequest(f"{skill} rank out of range: {rank}")
        if spec.get("enforce_build", True):
            total = sum(skills.values())
            if total > SKILL_POINTS:
                raise InvalidRequest(
                    f"skill points over budget: {total} of {SKILL_POINTS}"
                )

        return Character(
            id=spec["id"],
            name=spec["name"],
            attributes=dict(attributes),
            skills=dict(skills),
            hp=hp_max,
            hp_max=hp_max,
        )

    # --- checks ---

    def check_kinds(self) -> tuple[CheckKind, ...]:
        return CHECK_KINDS

    def _kind(self, kind_id: str) -> CheckKind:
        kind = _KINDS_BY_ID.get(kind_id)
        if kind is None:
            raise InvalidRequest(f"unknown check kind: {kind_id}")
        return kind

    def _condition_modifier(self, actor: Character, attribute: str) -> int:
        total = 0
        for active in actor.conditions:
            spec = _CONDITIONS_BY_ID.get(active.spec_id)
            if spec is None:
                continue
            if spec.affects_attributes and attribute not in spec.affects_attributes:
                continue
            total += spec.check_modifier
        return total

    def blocked_actions(self, actor: Character) -> frozenset[str]:
        blocked: set[str] = set()
        for active in actor.conditions:
            spec = _CONDITIONS_BY_ID.get(active.spec_id)
            if spec:
                blocked.update(spec.blocks_actions)
        return frozenset(blocked)

    def total_modifier(
        self, actor: Character, kind_id: str, situational: int = 0
    ) -> int:
        kind = self._kind(kind_id)
        return (
            actor.attribute_mod(kind.attribute)
            + actor.skills.get(kind_id, 0)
            + self._condition_modifier(actor, kind.attribute)
            + situational
        )

    def roll_required(
        self, actor: Character, request: CheckRequest
    ) -> tuple[bool, str | None]:
        kind = self._kind(request.kind_id)

        if kind.id in self.blocked_actions(actor):
            return False, f"blocked by a condition: cannot {kind.label.lower()}"

        modifier = self.total_modifier(actor, request.kind_id, request.situational)
        spread = request.dc - modifier

        # Absolute criticals make the gate load-bearing: without it, any task
        # is achievable by repetition.
        if spread > AUTO_FAIL_SPREAD:
            return False, "beyond this character's ability"
        if spread < AUTO_PASS_SPREAD:
            return False, "trivial for this character"
        return True, None

    def resolve_check(
        self, actor: Character, request: CheckRequest, rng: Rng
    ) -> CheckResult:
        kind = self._kind(request.kind_id)
        modifier = self.total_modifier(actor, request.kind_id, request.situational)

        should_roll, reason = self.roll_required(actor, request)
        if not should_roll:
            auto_tier = (
                Tier.SUCCESS if reason == "trivial for this character"
                else Tier.FAILURE
            )
            empty = roll_d20(_NullRng(), modifier=modifier, mode=request.mode)
            return CheckResult(
                tier=auto_tier,
                margin=modifier - request.dc,
                natural=0,
                dc=request.dc,
                roll=empty,
                reason=reason,
            )

        dice = roll_d20(rng, modifier=modifier, mode=request.mode)
        natural = dice.natural
        margin = dice.total - request.dc

        # Absolute override: the die face wins outright, before arithmetic.
        override = natural in (1, 20)
        if natural == 20:
            tier = Tier.CRIT_SUCCESS
        elif natural == 1:
            tier = Tier.CRIT_FAILURE
        else:
            tier = _tier_from_margin(margin)

        boon = setback = None
        if tier is Tier.CRIT_SUCCESS:
            boon = BoonSpec(
                kind=_BOON_BY_KIND.get(kind.id, BoonKind.EXTRA_INFORMATION),
                scale=_scale_for_dc(request.dc),
                hint=f"beyond what was sought when attempting to {kind.label.lower()}",
            )
        elif tier is Tier.CRIT_FAILURE:
            setback = SetbackSpec(
                kind=_SETBACK_BY_KIND.get(kind.id, SetbackKind.POSITION_LOST),
                scale=_scale_for_dc(request.dc),
                hint=f"a cost beyond mere failure when attempting to {kind.label.lower()}",
            )

        return CheckResult(
            tier=tier,
            margin=margin,
            natural=natural,
            dc=request.dc,
            roll=dice,
            override=override,
            boon=boon,
            setback=setback,
        )

    # --- state ---

    def condition_specs(self) -> tuple[ConditionSpec, ...]:
        return CONDITIONS

    def apply_consequence(self, actor: Character, result: CheckResult) -> StateDelta:
        """Only what the *system* mandates. Narrative fallout is separate."""
        if not result.rolled:
            return StateDelta()
        if result.tier is Tier.PARTIAL:
            # The cost of "yes, but" defaults to stress. The narrator may
            # propose a different cost, which the turn loop validates.
            return StateDelta(stress=1, notes=("partial success incurs a cost",))
        if result.tier is Tier.CRIT_FAILURE:
            return StateDelta(stress=2, notes=("critical failure",))
        return StateDelta()

    def tick_conditions(self, actor: Character) -> StateDelta:
        damage = 0
        expired: list[str] = []
        for active in actor.conditions:
            spec = _CONDITIONS_BY_ID.get(active.spec_id)
            if spec:
                damage += spec.damage_per_tick
            if active.tick() is None:
                expired.append(active.spec_id)
        return StateDelta(hp=-damage, remove_conditions=tuple(expired))

    def apply_delta(self, actor: Character, delta: StateDelta) -> Character:
        # Duration decrement lives in tick_conditions, never here - applying a
        # delta twice must not age conditions twice.
        conditions: list[ActiveCondition] = [
            c for c in actor.conditions if c.spec_id not in delta.remove_conditions
        ]
        for new in delta.add_conditions:
            if new.spec_id not in _CONDITIONS_BY_ID:
                raise InvalidRequest(f"unknown condition: {new.spec_id}")
            # Re-applying a condition refreshes it rather than stacking.
            conditions = [c for c in conditions if c.spec_id != new.spec_id]
            conditions.append(new)

        inventory = [i for i in actor.inventory if i not in delta.remove_items]
        inventory.extend(delta.add_items)

        return Character(
            id=actor.id,
            name=actor.name,
            attributes=actor.attributes,
            skills=actor.skills,
            hp=max(0, min(actor.hp_max, actor.hp + delta.hp)),
            hp_max=actor.hp_max,
            stress=max(0, min(actor.stress_max, actor.stress + delta.stress)),
            stress_max=actor.stress_max,
            conditions=tuple(conditions),
            inventory=tuple(inventory),
            level=actor.level,
        )

    # --- model-facing ---

    def describe_for_model(self, actor: Character) -> str:
        parts = [
            # The id is load-bearing, not decoration: every tool call takes an
            # actor_id, and a model that has only seen names will invent one.
            # A rejected call costs a whole extra round trip.
            f"{actor.name} (level {actor.level}) [actor_id: {actor.id}]",
            f"HP {actor.hp}/{actor.hp_max}, Stress {actor.stress}/{actor.stress_max}",
            "Attributes: "
            + ", ".join(
                f"{a} {actor.attributes.get(a, 10)} ({actor.attribute_mod(a):+d})"
                for a in ATTRIBUTES
            ),
        ]
        if actor.skills:
            parts.append(
                "Skills: "
                + ", ".join(f"{k} {v:+d}" for k, v in sorted(actor.skills.items()))
            )
        if actor.conditions:
            labels = []
            for active in actor.conditions:
                spec = _CONDITIONS_BY_ID.get(active.spec_id)
                label = spec.label if spec else active.spec_id
                if active.remaining_ticks is not None:
                    label += f" ({active.remaining_ticks})"
                labels.append(label)
            parts.append("Conditions: " + ", ".join(labels))
        if actor.inventory:
            parts.append("Carrying: " + ", ".join(actor.inventory))
        return "\n".join(parts)


def _tier_from_margin(margin: int) -> Tier:
    if margin >= CRIT_SUCCESS_MARGIN:
        return Tier.CRIT_SUCCESS
    if margin >= SUCCESS_MARGIN:
        return Tier.SUCCESS
    if margin > PARTIAL_MARGIN:
        return Tier.PARTIAL
    if margin > CRIT_FAILURE_MARGIN:
        return Tier.FAILURE
    return Tier.CRIT_FAILURE


class _NullRng:
    """Used when a check is gated and no dice are thrown."""

    def randint(self, a: int, b: int) -> int:
        return a
