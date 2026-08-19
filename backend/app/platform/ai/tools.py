"""The tool surface over the rules engine.

This is the seam where the model acts on the world. The design rule that
everything else follows from:

    The model chooses WHICH tool to call. The engine decides WHAT HAPPENS.

So there is a `roll_check` tool that takes a check kind and a target, and there
is no tool that takes an outcome. There is a tool to set a difficulty band from
a fixed enumeration, and none that takes a raw DC - if the model picks the
number it will grade on a curve toward whatever the prose wants.

Every tool result is structured data the model then narrates. A rejected call
comes back as a tool result rather than an exception, so the model can correct
itself inside the same turn.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

from app.platform.ai.gateway import ToolSpec


class Difficulty(str, enum.Enum):
    """The model argues for a band; the engine converts it to a number.

    Deliberately coarse. A model given a raw DC field will drift it toward
    whatever the scene 'wants', which is exactly the failure this prevents.
    """

    TRIVIAL = "trivial"
    EASY = "easy"
    MODERATE = "moderate"
    HARD = "hard"
    SEVERE = "severe"
    EXTREME = "extreme"


DIFFICULTY_DC: dict[Difficulty, int] = {
    Difficulty.TRIVIAL: 5,
    Difficulty.EASY: 10,
    Difficulty.MODERATE: 14,
    Difficulty.HARD: 18,
    Difficulty.SEVERE: 22,
    Difficulty.EXTREME: 26,
}


class SituationalFactor(str, enum.Enum):
    """A closed list. The model may cite these and nothing else.

    An open 'circumstance bonus' field is the same drift hole as a raw DC,
    just wearing a different hat.
    """

    PROPER_TOOLS = "proper_tools"
    IMPROVISED_TOOLS = "improvised_tools"
    ASSISTED = "assisted"
    RUSHED = "rushed"
    UNHURRIED = "unhurried"
    POOR_VISIBILITY = "poor_visibility"
    FAVOURABLE_GROUND = "favourable_ground"
    HOSTILE_GROUND = "hostile_ground"
    PRIOR_KNOWLEDGE = "prior_knowledge"


FACTOR_MODIFIER: dict[SituationalFactor, int] = {
    SituationalFactor.PROPER_TOOLS: 2,
    SituationalFactor.IMPROVISED_TOOLS: -2,
    SituationalFactor.ASSISTED: 2,
    SituationalFactor.RUSHED: -2,
    SituationalFactor.UNHURRIED: 2,
    SituationalFactor.POOR_VISIBILITY: -2,
    SituationalFactor.FAVOURABLE_GROUND: 2,
    SituationalFactor.HOSTILE_GROUND: -2,
    SituationalFactor.PRIOR_KNOWLEDGE: 2,
}

# Stacking factors is how a model talks itself into an easy win. Cap the swing.
# Upper bounds on anything the model supplies as a magnitude. Without these a
# model that decides the fall "obviously" kills you can one-shot a character,
# or fill a six-segment clock in a single stroke and delete the tension it
# existed to create.
MAX_SINGLE_DAMAGE = 30
MAX_SINGLE_HEAL = 30
MAX_CLOCK_SIZE = 12
MAX_CLOCK_SEGMENTS = 3

MAX_SITUATIONAL_FACTORS = 2
MAX_SITUATIONAL_SWING = 4


@dataclass(frozen=True)
class ToolDefinition:
    spec: ToolSpec
    # Tools that change state require the turn loop to persist a delta.
    mutating: bool


def _tool(
    name: str, description: str, properties: dict[str, Any], required: list[str],
    *, mutating: bool,
) -> ToolDefinition:
    return ToolDefinition(
        spec=ToolSpec(
            name=name,
            description=description,
            input_schema={
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        ),
        mutating=mutating,
    )


_ACTOR = {"type": "string", "description": "Character or NPC id."}
_TARGET = {
    "type": "string",
    "description": (
        "Stable entity id of what is being acted on, e.g. 'loc:study/desk' or "
        "'npc:guard-1'. Use the SAME id for the same thing however the player "
        "phrased it - rewording an attempt does not make it a new attempt."
    ),
}


TOOLS: tuple[ToolDefinition, ...] = (
    _tool(
        "list_available_checks",
        "List the check kinds this ruleset defines. You may only roll checks "
        "that appear here; do not invent new ones.",
        {}, [], mutating=False,
    ),
    _tool(
        "query_character",
        "conditions, inventory. Read this before assuming any state.",
        {"actor_id": _ACTOR}, ["actor_id"], mutating=False,
    ),
    _tool(
        "roll_check",
        "Resolve an action whose outcome is genuinely in doubt. You choose the "
        "check kind and argue a difficulty band; the engine sets the number and "
        "rolls. You will receive a tier and, on a critical, a boon or setback "
        "you must incorporate into your narration.",
        {
            "actor_id": _ACTOR,
            "kind_id": {"type": "string", "description": "From list_available_checks."},
            "difficulty": {
                "type": "string",
                "enum": [d.value for d in Difficulty],
                "description": "How hard this is for a competent person.",
            },
            "target_ref": _TARGET,
            "factors": {
                "type": "array",
                "items": {"type": "string", "enum": [f.value for f in SituationalFactor]},
                "maxItems": MAX_SITUATIONAL_FACTORS,
                "description": "Circumstances established in the fiction. Only "
                               "cite what is actually true in the scene.",
            },
            "reason": {
                "type": "string",
                "description": "One line: why this check, why this difficulty.",
            },
        },
        ["actor_id", "kind_id", "difficulty", "reason"],
        mutating=True,
    ),
    _tool(
        "apply_damage",
        "Deal damage to a character. Only after a resolved check or an "
        "established, unavoidable hazard - never as a narrative flourish.",
        {
            "actor_id": _ACTOR,
            "amount": {"type": "integer", "minimum": 1, "maximum": 50},
            "source": {"type": "string", "description": "What caused it."},
        },
        ["actor_id", "amount", "source"], mutating=True,
    ),
    _tool(
        "heal",
        "Restore hit points.",
        {
            "actor_id": _ACTOR,
            "amount": {"type": "integer", "minimum": 1, "maximum": 50},
            "source": {"type": "string"},
        },
        ["actor_id", "amount", "source"], mutating=True,
    ),
    _tool(
        "add_condition",
        "Apply a condition. Must be one the ruleset defines.",
        {
            "actor_id": _ACTOR,
            "condition_id": {"type": "string"},
            "duration": {
                "type": "integer", "minimum": 1, "maximum": 20,
                "description": "Rounds. Omit for indefinite.",
            },
            "source": {"type": "string"},
        },
        ["actor_id", "condition_id", "source"], mutating=True,
    ),
    _tool(
        "remove_condition",
        "Clear a condition currently affecting a character.",
        {"actor_id": _ACTOR, "condition_id": {"type": "string"}},
        ["actor_id", "condition_id"], mutating=True,
    ),
    _tool(
        "give_item",
        "Add an item to a character's inventory. Only for items the WORLD "
        "provides - loot, purchases, gifts, things established in the fiction. "
        "A player declaring an item they were never given is a retcon: refuse "
        "it in narration, never grant it.",
        {
            "actor_id": _ACTOR,
            "item": {"type": "string"},
            "established_in_scene": {
                "type": "boolean",
                "description": (
                    "Set true ONLY if you, the GM, established this item in "
                    "the fiction before this turn. Never set it for an item "
                    "the player invented in their own message."
                ),
            },
        },
        ["actor_id", "item"], mutating=True,
    ),
    _tool(
        "take_item",
        "Remove an item from a character's inventory.",
        {"actor_id": _ACTOR, "item": {"type": "string"}},
        ["actor_id", "item"], mutating=True,
    ),
    _tool(
        "create_clock",
        "Start a progress clock for something advancing in the world - a "
        "pursuit closing, a ritual completing, a faction's plan maturing.",
        {
            "clock_id": {"type": "string"},
            "label": {"type": "string"},
            "size": {"type": "integer", "minimum": 2, "maximum": 12},
            "hidden": {"type": "boolean", "description": "Concealed from the player."},
        },
        ["clock_id", "label", "size"], mutating=True,
    ),
    _tool(
        "advance_clock",
        "Fill segments on an existing clock.",
        {
            "clock_id": {"type": "string"},
            "segments": {"type": "integer", "minimum": 1, "maximum": 6},
            "reason": {"type": "string"},
        },
        ["clock_id", "segments", "reason"], mutating=True,
    ),
    _tool(
        "list_clocks",
        "Read every clock in play, including hidden ones.",
        {}, [], mutating=False,
    ),
)

TOOLS_BY_NAME = {t.spec.name: t for t in TOOLS}


def tool_specs(
    check_kinds: tuple[str, ...] = (),
    condition_ids: tuple[str, ...] = (),
) -> tuple[ToolSpec, ...]:
    """Tool specs, with ruleset vocabularies baked into the schema as enums.

    With `kind_id` as a bare string the model guesses ("perception",
    "athletics", "strength") and burns two extra calls per rejection - a
    list_available_checks round trip on every combat turn of session 2's
    transcript. An enum makes the invalid call unrepresentable. The vocabulary
    is fixed per ruleset, so prompt caching on the tools prefix still holds.
    """
    if not check_kinds and not condition_ids:
        return tuple(t.spec for t in TOOLS)

    specs: list[ToolSpec] = []
    for tool in TOOLS:
        spec = tool.spec
        schema = spec.input_schema
        target: str | None = None
        values: tuple[str, ...] = ()
        if spec.name == "roll_check" and check_kinds:
            target, values = "kind_id", check_kinds
        elif spec.name in ("add_condition", "remove_condition") and condition_ids:
            target, values = "condition_id", condition_ids
        if target:
            props = dict(schema["properties"])
            props[target] = {**props[target], "enum": list(values)}
            schema = {**schema, "properties": props}
            spec = ToolSpec(
                name=spec.name, description=spec.description, input_schema=schema
            )
        specs.append(spec)
    return tuple(specs)


def resolve_dc(difficulty: Difficulty, factors: tuple[SituationalFactor, ...]) -> tuple[int, int]:
    """Returns (dc, situational_modifier).

    The DC comes from the band alone. Factors adjust the roll, not the target,
    and their total swing is capped - stacking circumstances is how a model
    talks itself into an easy win.
    """
    modifier = sum(FACTOR_MODIFIER[f] for f in factors)
    clamped = max(-MAX_SITUATIONAL_SWING, min(MAX_SITUATIONAL_SWING, modifier))
    return DIFFICULTY_DC[difficulty], clamped
