"""Tool execution and validation.

Every call the model makes passes through here before it touches anything. A
rejected call comes back as a structured tool *result*, not an exception - the
model reads the rejection and corrects itself inside the same turn, which is
both cheaper and more robust than failing the whole request.

Rejections are counted on `augur_rule_violations_total{source="model"}`. That
number is the early warning that a prompt has drifted: a healthy session
produces almost none, and a spike means the model is trying things the rules
forbid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.modules.rules.engine import RulesEngine
from app.modules.rules.locking import Situation
from app.modules.rules.types import (
    ActiveCondition,
    Character,
    CheckRequest,
    Clock,
    StateDelta,
)
from app.platform.ai.tools import (
    MAX_CLOCK_SEGMENTS,
    MAX_CLOCK_SIZE,
    MAX_SINGLE_DAMAGE,
    MAX_SINGLE_HEAL,
    MAX_SITUATIONAL_FACTORS,
    TOOLS_BY_NAME,
    Difficulty,
    SituationalFactor,
    resolve_dc,
)
from app.platform.observability.metrics import rule_violations


@dataclass
class TurnScope:
    """What the model is permitted to touch this turn.

    Authority is scoped per turn rather than per session: the model may act on
    actors present in this scene and no others. Without this, a model that
    hallucinates an actor id can quietly mutate a character who is not even
    in the room.
    """

    scene_id: str
    characters: dict[str, Character]
    clocks: dict[str, Clock] = field(default_factory=dict)
    # Entity ids the model may reference as targets. Empty tuple = unrestricted,
    # which is the Milestone 3 state; Milestone 4 populates it from the scene.
    known_targets: tuple[str, ...] = ()
    situation: Situation | None = None

    def situation_for(self, actor: Character) -> Situation:
        if self.situation is not None:
            return self.situation
        return Situation(
            scene_id=self.scene_id,
            condition_ids=tuple(sorted(c.spec_id for c in actor.conditions)),
            relevant_assets=tuple(sorted(actor.inventory)),
        )


@dataclass
class ToolOutcome:
    """Result of one tool call, ready to hand back to the model."""

    name: str
    ok: bool
    payload: dict[str, Any]
    delta: StateDelta | None = None
    actor_id: str | None = None
    clock: Clock | None = None

    def to_tool_result(self) -> dict[str, Any]:
        if self.ok:
            return self.payload
        return {"error": self.payload.get("error"), "hint": self.payload.get("hint", "")}


class ToolRejection(Exception):
    """Internal control flow. Never escapes the executor."""

    def __init__(self, error: str, hint: str = "") -> None:
        super().__init__(error)
        self.error = error
        self.hint = hint


# Per-tier narration guidance. Never mention the tier, the number, or the word
# "check" - the dice are already shown to the player separately, and repeating
# them in prose is how a game master starts sounding like a spreadsheet.
_NARRATION_GUIDANCE = {
    "crit_success": (
        "It goes better than they had any right to expect. Show that, do not "
        "say it."
    ),
    "success": "It works. Show it working, and move the scene forward.",
    "partial": (
        "They get what they wanted AND it costs them something concrete - "
        "noise, time, a broken tool, someone noticing. Name the cost."
    ),
    "failure": (
        "It does not work. Do not stall the scene: the failure should change "
        "the situation, not just deny it."
    ),
    "crit_failure": (
        "It fails and makes things worse in a way they will have to deal with."
    ),
}


class ToolExecutor:
    def __init__(self, engine: RulesEngine) -> None:
        self._engine = engine

    # -------------------------------------------------------------- dispatch

    def execute(
        self, name: str, arguments: dict[str, Any], scope: TurnScope
    ) -> ToolOutcome:
        definition = TOOLS_BY_NAME.get(name)
        if definition is None:
            return self._reject(name, f"no such tool: {name}",
                                "Call only the tools you were given.")
        try:
            handler = getattr(self, f"_do_{name}")
            return handler(arguments, scope)
        except ToolRejection as rejection:
            return self._reject(name, rejection.error, rejection.hint)

    def _reject(self, name: str, error: str, hint: str = "") -> ToolOutcome:
        rule_violations.labels(source="model").inc()
        return ToolOutcome(name=name, ok=False, payload={"error": error, "hint": hint})

    # -------------------------------------------------------------- guards

    def _actor(self, arguments: dict[str, Any], scope: TurnScope) -> Character:
        actor_id = arguments.get("actor_id")
        actor = scope.characters.get(actor_id)
        if actor is None:
            raise ToolRejection(
                f"no actor '{actor_id}' in this scene",
                f"Actors present: {', '.join(sorted(scope.characters)) or 'none'}",
            )
        return actor

    def _validate_target(self, target_ref: str | None, scope: TurnScope) -> str | None:
        if target_ref is None or not scope.known_targets:
            return target_ref
        if target_ref not in scope.known_targets:
            raise ToolRejection(
                f"unknown target '{target_ref}' in this scene",
                f"Known targets: {', '.join(scope.known_targets)}",
            )
        return target_ref

    def _factors(self, raw: Any) -> tuple[SituationalFactor, ...]:
        if raw is None:
            return ()
        if not isinstance(raw, list):
            raise ToolRejection("factors must be a list")
        if len(raw) > MAX_SITUATIONAL_FACTORS:
            raise ToolRejection(
                f"at most {MAX_SITUATIONAL_FACTORS} situational factors",
                "Cite only the circumstances that genuinely apply.",
            )
        factors = []
        for item in raw:
            try:
                factors.append(SituationalFactor(item))
            except ValueError:
                raise ToolRejection(
                    f"unknown situational factor: {item}",
                    f"Valid: {', '.join(f.value for f in SituationalFactor)}",
                ) from None
        if len(set(factors)) != len(factors):
            raise ToolRejection("duplicate situational factors")
        return tuple(factors)

    # -------------------------------------------------------------- read-only

    def _do_list_available_checks(
        self, arguments: dict, scope: TurnScope
    ) -> ToolOutcome:
        """Offers only what the actor can currently attempt.

        A blinded character listed as able to search invites the model to call
        a check that the engine will then refuse - a wasted round trip and a
        confusing rejection. Filtering here keeps the model's options honest.
        """
        blocked: frozenset[str] = frozenset()
        actor_id = arguments.get("actor_id")
        if actor_id:
            actor = scope.characters.get(actor_id)
            if actor is None:
                raise ToolRejection(
                    f"no actor '{actor_id}' in this scene",
                    f"Present: {', '.join(scope.characters) or 'none'}",
                )
            blocked = getattr(self._engine.ruleset, "blocked_actions", lambda _: frozenset())(actor)

        return ToolOutcome(
            "list_available_checks", True,
            {
                "checks": [
                    {"id": k.id, "label": k.label, "attribute": k.attribute,
                     "repeatable": k.lock_policy.value == "never"}
                    for k in self._engine.ruleset.check_kinds()
                    if k.id not in blocked
                ],
                "blocked": sorted(blocked),
            },
        )

    def _do_query_character(self, arguments: dict, scope: TurnScope) -> ToolOutcome:
        actor = self._actor(arguments, scope)
        return ToolOutcome(
            "query_character", True,
            {
                "sheet": self._engine.ruleset.describe_for_model(actor),
                "hp": actor.hp,
                "hp_max": actor.hp_max,
                "conditions": [c.spec_id for c in actor.conditions],
                "inventory": list(actor.inventory),
            },
        )

    def _do_list_clocks(self, _: dict, scope: TurnScope) -> ToolOutcome:
        return ToolOutcome(
            "list_clocks", True,
            {"clocks": [
                {"id": c.id, "label": c.label, "filled": c.filled,
                 "size": c.size, "hidden": c.hidden, "complete": c.complete}
                for c in scope.clocks.values()
            ]},
        )

    # -------------------------------------------------------------- the check

    def _do_roll_check(self, arguments: dict, scope: TurnScope) -> ToolOutcome:
        actor = self._actor(arguments, scope)

        kind_id = arguments.get("kind_id")
        if kind_id not in {k.id for k in self._engine.ruleset.check_kinds()}:
            raise ToolRejection(
                f"unknown check kind: {kind_id}",
                "Call list_available_checks and choose from those.",
            )

        try:
            difficulty = Difficulty(arguments.get("difficulty"))
        except ValueError:
            raise ToolRejection(
                f"unknown difficulty: {arguments.get('difficulty')}",
                f"Valid: {', '.join(d.value for d in Difficulty)}",
            ) from None

        if not (arguments.get("reason") or "").strip():
            raise ToolRejection(
                "reason is required",
                "State in one line why this check and this difficulty.",
            )

        factors = self._factors(arguments.get("factors"))
        target_ref = self._validate_target(arguments.get("target_ref"), scope)

        # The band sets the DC. The model never supplies a number.
        dc, situational = resolve_dc(difficulty, factors)

        resolution = self._engine.resolve(
            actor,
            CheckRequest(
                actor_id=actor.id, kind_id=kind_id, dc=dc,
                target_ref=target_ref, situational=situational,
            ),
            scope.situation_for(actor),
        )

        payload = resolution.result.to_narrator()
        payload["difficulty"] = difficulty.value
        payload["locked"] = resolution.locked

        # Every tier gets an instruction, not just the criticals. Left to
        # itself a model reports the outcome ("the check succeeded") because
        # that is what the tool result literally says - and a reported roll is
        # the least interesting thing that can happen at a table.
        tier = resolution.result.tier
        payload["instruction"] = _NARRATION_GUIDANCE.get(
            tier.value, "Narrate what this means in the fiction."
        )
        if resolution.locked:
            payload["instruction"] = (
                "This was already attempted under the same circumstances. "
                "Describe finding nothing new rather than re-resolving it."
            )
        if resolution.result.boon:
            payload["instruction"] += (
                " Then deliver the boon: something beyond what was sought, at "
                "the stated scale, as a specific thing in the world."
            )
        if resolution.result.setback:
            payload["instruction"] += (
                " Then deliver the setback: a cost beyond merely not "
                "succeeding, at the stated scale, that they can point at."
            )

        return ToolOutcome(
            "roll_check", True, payload,
            delta=resolution.delta, actor_id=actor.id,
        )

    # -------------------------------------------------------------- mutations

    def _do_apply_damage(self, arguments: dict, scope: TurnScope) -> ToolOutcome:
        actor = self._actor(arguments, scope)
        amount = int(arguments.get("amount", 0))
        if amount < 1:
            raise ToolRejection("damage must be at least 1")
        if amount > MAX_SINGLE_DAMAGE:
            raise ToolRejection(
                f"damage above {MAX_SINGLE_DAMAGE} in one call is not permitted",
                "Apply damage in proportion to the fiction, or use a clock for "
                "an escalating threat.",
            )
        return ToolOutcome(
            "apply_damage", True,
            {"actor_id": actor.id, "damage": amount,
             "hp_after": max(0, actor.hp - amount),
             "defeated": actor.hp - amount <= 0},
            delta=StateDelta(hp=-amount, notes=(f"damage: {arguments.get('source')}",)),
            actor_id=actor.id,
        )

    def _do_heal(self, arguments: dict, scope: TurnScope) -> ToolOutcome:
        actor = self._actor(arguments, scope)
        amount = int(arguments.get("amount", 0))
        if amount < 1:
            raise ToolRejection("healing must be at least 1")
        if amount > MAX_SINGLE_HEAL:
            raise ToolRejection(
                f"healing above {MAX_SINGLE_HEAL} in one call is not permitted"
            )
        return ToolOutcome(
            "heal", True,
            {"actor_id": actor.id, "healed": amount,
             "hp_after": min(actor.hp_max, actor.hp + amount)},
            delta=StateDelta(hp=amount, notes=(f"healed: {arguments.get('source')}",)),
            actor_id=actor.id,
        )

    def _do_add_condition(self, arguments: dict, scope: TurnScope) -> ToolOutcome:
        actor = self._actor(arguments, scope)
        condition_id = arguments.get("condition_id")
        known = {c.id for c in self._engine.ruleset.condition_specs()}
        if condition_id not in known:
            raise ToolRejection(
                f"unknown condition: {condition_id}",
                f"Valid: {', '.join(sorted(known))}",
            )
        duration = arguments.get("duration")
        return ToolOutcome(
            "add_condition", True,
            {"actor_id": actor.id, "condition": condition_id, "duration": duration},
            delta=StateDelta(add_conditions=(
                ActiveCondition(condition_id, duration, arguments.get("source", "")),
            )),
            actor_id=actor.id,
        )

    def _do_remove_condition(self, arguments: dict, scope: TurnScope) -> ToolOutcome:
        actor = self._actor(arguments, scope)
        condition_id = arguments.get("condition_id")
        if condition_id not in {c.spec_id for c in actor.conditions}:
            raise ToolRejection(
                f"{actor.name} is not affected by {condition_id}",
                f"Active: {', '.join(c.spec_id for c in actor.conditions) or 'none'}",
            )
        return ToolOutcome(
            "remove_condition", True,
            {"actor_id": actor.id, "cleared": condition_id},
            delta=StateDelta(remove_conditions=(condition_id,)),
            actor_id=actor.id,
        )

    def _do_give_item(self, arguments: dict, scope: TurnScope) -> ToolOutcome:
        actor = self._actor(arguments, scope)
        item = (arguments.get("item") or "").strip()
        if not item:
            raise ToolRejection("item must not be empty")
        return ToolOutcome(
            "give_item", True, {"actor_id": actor.id, "item": item},
            delta=StateDelta(add_items=(item,)), actor_id=actor.id,
        )

    def _do_take_item(self, arguments: dict, scope: TurnScope) -> ToolOutcome:
        actor = self._actor(arguments, scope)
        item = (arguments.get("item") or "").strip()
        if item not in actor.inventory:
            raise ToolRejection(
                f"{actor.name} is not carrying '{item}'",
                f"Carrying: {', '.join(actor.inventory) or 'nothing'}",
            )
        return ToolOutcome(
            "take_item", True, {"actor_id": actor.id, "item": item},
            delta=StateDelta(remove_items=(item,)), actor_id=actor.id,
        )

    # -------------------------------------------------------------- clocks

    def _do_create_clock(self, arguments: dict, scope: TurnScope) -> ToolOutcome:
        clock_id = arguments.get("clock_id")
        if clock_id in scope.clocks:
            raise ToolRejection(
                f"clock '{clock_id}' already exists",
                "Use advance_clock to progress an existing clock.",
            )
        size = int(arguments.get("size", 4))
        if size > MAX_CLOCK_SIZE:
            raise ToolRejection(
                f"clock size above {MAX_CLOCK_SIZE} is not permitted",
                "Clocks are dramatic pacing, not counters. Four, six or eight.",
            )
        try:
            clock = Clock(
                id=clock_id, label=arguments.get("label", ""),
                size=size,
                hidden=bool(arguments.get("hidden", False)),
            )
        except ValueError as exc:
            raise ToolRejection(str(exc)) from None
        return ToolOutcome(
            "create_clock", True,
            {"clock_id": clock.id, "label": clock.label, "size": clock.size},
            clock=clock,
        )

    def _do_advance_clock(self, arguments: dict, scope: TurnScope) -> ToolOutcome:
        clock_id = arguments.get("clock_id")
        clock = scope.clocks.get(clock_id)
        if clock is None:
            raise ToolRejection(
                f"no clock '{clock_id}'",
                f"Existing: {', '.join(scope.clocks) or 'none'}",
            )
        segments = int(arguments.get("segments", 1))
        if segments < 1:
            raise ToolRejection("segments must be at least 1")
        if segments > MAX_CLOCK_SEGMENTS:
            raise ToolRejection(
                f"a clock advances at most {MAX_CLOCK_SEGMENTS} segments per call",
                "Filling a clock in one stroke removes the tension it exists for.",
            )
        advanced = clock.advance(segments)
        return ToolOutcome(
            "advance_clock", True,
            {"clock_id": advanced.id, "label": advanced.label,
             "filled": advanced.filled, "size": advanced.size,
             "complete": advanced.complete},
            clock=advanced,
        )
