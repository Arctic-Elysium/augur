"""Tool executor tests.

Written adversarially. This layer's whole job is to be the thing standing
between a model that will confidently do the wrong thing and a database, so
the tests assume the model is hostile rather than merely mistaken.

The governing rule under test throughout:

    The model chooses WHICH tool to call. The engine decides WHAT HAPPENS.
"""

from __future__ import annotations

import pytest

from app.modules.rules.engine import RulesEngine
from app.modules.rules.locking import CheckLedger
from app.modules.rules.types import ActiveCondition, Character, Clock, Tier
from app.platform.ai.executor import ToolExecutor, TurnScope
from app.platform.ai.tools import TOOLS_BY_NAME, Difficulty, resolve_dc


class Scripted:
    def __init__(self, *values: int) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0) if self._values else 10


def character(actor_id="pc-1", **overrides) -> Character:
    base = {
        "id": actor_id,
        "name": "Vessa",
        "attributes": {
            "strength": 11, "dexterity": 15, "constitution": 13,
            "intelligence": 12, "wisdom": 16, "charisma": 9,
        },
        "skills": {"search": 2},
        "hp": 12,
        "hp_max": 12,
    }
    base.update(overrides)
    return Character(**base)


def scope_with(*characters_: Character, clocks=None, **overrides) -> TurnScope:
    base = {
        "scene_id": "scene-1",
        "characters": {c.id: c for c in characters_},
        "clocks": {c.id: c for c in (clocks or [])},
    }
    base.update(overrides)
    return TurnScope(**base)


def executor(*rolls: int) -> ToolExecutor:
    return ToolExecutor(RulesEngine(ledger=CheckLedger(), rng=Scripted(*rolls)))


# ------------------------------------------------------------ the core invariant


def test_no_tool_accepts_a_raw_dc():
    """If the model picks the number it grades on a curve toward the prose."""
    for name, spec in TOOLS_BY_NAME.items():
        properties = spec.spec.input_schema.get("properties", {})
        assert "dc" not in properties, f"{name} exposes a raw DC to the model"
        assert "difficulty_class" not in properties, f"{name} exposes a raw DC"


def test_no_tool_accepts_an_outcome():
    """There is no tool that takes a result. Outcomes are computed, not given."""
    forbidden = {"tier", "outcome", "result", "success", "succeeded"}
    for name, spec in TOOLS_BY_NAME.items():
        properties = set(spec.spec.input_schema.get("properties", {}))
        overlap = properties & forbidden
        assert not overlap, f"{name} lets the model dictate an outcome: {overlap}"


def test_difficulty_band_maps_to_a_fixed_dc():
    """Bands are coarse on purpose; a model given fine control drifts it."""
    values = {d: resolve_dc(d, ())[0] for d in Difficulty}
    assert values[Difficulty.TRIVIAL] < values[Difficulty.MODERATE]
    assert values[Difficulty.MODERATE] < values[Difficulty.EXTREME]
    # Same band, same number, every time.
    assert resolve_dc(Difficulty.HARD, ()) == resolve_dc(Difficulty.HARD, ())


# ------------------------------------------------------------ scope enforcement


def test_unknown_actor_is_rejected():
    """A hallucinated actor id must not silently mutate someone off-screen."""
    outcome = executor().execute(
        "apply_damage", {"actor_id": "pc-nobody", "amount": 5}, scope_with(character())
    )
    assert outcome.ok is False
    assert outcome.delta is None
    assert "pc-nobody" in outcome.payload["error"]


def test_actor_outside_the_scene_is_rejected():
    present = character("pc-1")
    absent = character("pc-2")
    outcome = executor().execute(
        "apply_damage", {"actor_id": absent.id, "amount": 5}, scope_with(present)
    )
    assert outcome.ok is False


def test_unknown_tool_is_rejected_not_raised():
    outcome = executor().execute("delete_everything", {}, scope_with(character()))
    assert outcome.ok is False
    assert "no such tool" in outcome.payload["error"]


def test_rejection_returns_a_tool_result_rather_than_raising():
    """The model reads the rejection and corrects itself in the same turn."""
    outcome = executor().execute("roll_check", {}, scope_with(character()))
    assert outcome.ok is False
    payload = outcome.to_tool_result()
    assert "error" in payload
    assert "hint" in payload


def test_rejections_increment_the_violation_metric():
    from app.platform.observability.metrics import rule_violations

    before = rule_violations.labels(source="model")._value.get()
    executor().execute("nonexistent", {}, scope_with(character()))
    after = rule_violations.labels(source="model")._value.get()
    assert after == before + 1


# ------------------------------------------------------------ check resolution


def test_roll_check_resolves_through_the_engine():
    outcome = executor(18).execute(
        "roll_check",
        {"actor_id": "pc-1", "kind_id": "search",
         "difficulty": "moderate", "target_ref": "loc:study/desk", "reason": "test"},
        scope_with(character()),
    )
    assert outcome.ok is True
    assert "tier" in outcome.payload


def test_unknown_check_kind_is_rejected():
    outcome = executor().execute(
        "roll_check",
        {"actor_id": "pc-1", "kind_id": "telepathy", "difficulty": "moderate", "reason": "test"},
        scope_with(character()),
    )
    assert outcome.ok is False


def test_invalid_difficulty_band_is_rejected():
    outcome = executor().execute(
        "roll_check",
        {"actor_id": "pc-1", "kind_id": "search", "difficulty": "impossible", "reason": "test"},
        scope_with(character()),
    )
    assert outcome.ok is False


def test_repeated_check_reports_the_lock_to_the_model():
    """The model must be told it is a locked repeat, not handed a fresh roll."""
    ex = executor(4, 20, 20)
    scope = scope_with(character())
    args = {"actor_id": "pc-1", "kind_id": "search", "difficulty": "hard",
            "target_ref": "loc:study/desk", "reason": "searching the desk"}

    first = ex.execute("roll_check", args, scope)
    repeat = ex.execute("roll_check", args, scope)

    assert first.ok and repeat.ok
    assert repeat.payload.get("locked") is True
    assert repeat.payload["tier"] == first.payload["tier"]


def test_natural_20_cannot_rescue_an_impossible_check():
    """The roll gate, reached through the tool surface."""
    weak = character(attributes={"strength": 3, "dexterity": 3, "constitution": 3,
                                 "intelligence": 3, "wisdom": 3, "charisma": 3},
                     skills={})
    outcome = executor(20).execute(
        "roll_check",
        {"actor_id": "pc-1", "kind_id": "search", "difficulty": "extreme", "reason": "test"},
        scope_with(weak),
    )
    assert outcome.payload["tier"] != Tier.CRIT_SUCCESS.value


# ------------------------------------------------------------ state mutation


def test_damage_produces_a_delta_rather_than_mutating():
    actor = character(hp=12)
    outcome = executor().execute(
        "apply_damage", {"actor_id": actor.id, "amount": 4}, scope_with(actor)
    )
    assert outcome.ok is True
    assert outcome.delta.hp == -4
    assert actor.hp == 12, "the executor must never mutate in place"


def test_negative_damage_is_rejected():
    """Otherwise 'damage' becomes an unbounded healing tool."""
    outcome = executor().execute(
        "apply_damage", {"actor_id": "pc-1", "amount": -50}, scope_with(character())
    )
    assert outcome.ok is False


def test_absurd_damage_is_rejected():
    outcome = executor().execute(
        "apply_damage", {"actor_id": "pc-1", "amount": 99999}, scope_with(character())
    )
    assert outcome.ok is False


def test_unknown_condition_is_rejected():
    outcome = executor().execute(
        "add_condition",
        {"actor_id": "pc-1", "condition_id": "cursed_by_the_gods", "source": "x"},
        scope_with(character()),
    )
    assert outcome.ok is False
    assert outcome.delta is None


def test_known_condition_is_applied():
    outcome = executor().execute(
        "add_condition",
        {"actor_id": "pc-1", "condition_id": "poisoned", "duration": 3, "source": "the venom"},
        scope_with(character()),
    )
    assert outcome.ok is True
    assert outcome.delta.add_conditions[0].spec_id == "poisoned"


def test_condition_removal_requires_it_to_be_present():
    outcome = executor().execute(
        "remove_condition",
        {"actor_id": "pc-1", "condition_id": "poisoned"},
        scope_with(character()),
    )
    assert outcome.ok is False


def test_condition_removal_succeeds_when_present():
    actor = character(conditions=(ActiveCondition("poisoned"),))
    outcome = executor().execute(
        "remove_condition",
        {"actor_id": actor.id, "condition_id": "poisoned"},
        scope_with(actor),
    )
    assert outcome.ok is True
    assert "poisoned" in outcome.delta.remove_conditions


def test_taking_an_item_the_actor_lacks_is_rejected():
    outcome = executor().execute(
        "take_item", {"actor_id": "pc-1", "item": "a sword they never had"},
        scope_with(character()),
    )
    assert outcome.ok is False


def test_taking_a_carried_item_succeeds():
    actor = character(inventory=("rope", "lantern"))
    outcome = executor().execute(
        "take_item", {"actor_id": actor.id, "item": "rope"}, scope_with(actor)
    )
    assert outcome.ok is True
    assert "rope" in outcome.delta.remove_items


# ------------------------------------------------------------ clocks


def test_advancing_an_unknown_clock_is_rejected():
    outcome = executor().execute(
        "advance_clock", {"clock_id": "clock-nowhere", "segments": 1},
        scope_with(character()),
    )
    assert outcome.ok is False


def test_clock_advances_within_bounds():
    clock = Clock("pursuit", "The Watch closes in", size=6, filled=2)
    outcome = executor().execute(
        "advance_clock", {"clock_id": "pursuit", "segments": 2},
        scope_with(character(), clocks=[clock]),
    )
    assert outcome.ok is True
    assert outcome.clock.filled == 4
    assert clock.filled == 2, "the original clock must not be mutated"


def test_clock_cannot_be_advanced_by_an_absurd_amount():
    clock = Clock("pursuit", "The Watch closes in", size=6)
    outcome = executor().execute(
        "advance_clock", {"clock_id": "pursuit", "segments": 500},
        scope_with(character(), clocks=[clock]),
    )
    assert outcome.ok is False


def test_created_clock_has_a_sane_size():
    outcome = executor().execute(
        "create_clock",
        {"clock_id": "ritual", "label": "The ritual completes", "size": 900},
        scope_with(character()),
    )
    assert outcome.ok is False


# ------------------------------------------------------------ read-only tools


def test_query_character_returns_state_without_a_delta():
    outcome = executor().execute(
        "query_character", {"actor_id": "pc-1"}, scope_with(character())
    )
    assert outcome.ok is True
    assert outcome.delta is None
    assert "Vessa" in str(outcome.payload)


def test_list_available_checks_excludes_blocked_actions():
    """A blinded character should not be offered a search."""
    blinded = character(conditions=(ActiveCondition("blinded"),))
    outcome = executor().execute(
        "list_available_checks", {"actor_id": blinded.id}, scope_with(blinded)
    )
    assert outcome.ok is True
    offered = {c["id"] if isinstance(c, dict) else c for c in outcome.payload["checks"]}
    assert "search" not in offered


def test_list_clocks_reports_visible_state():
    clock = Clock("pursuit", "The Watch closes in", size=6, filled=3)
    outcome = executor().execute(
        "list_clocks", {}, scope_with(character(), clocks=[clock])
    )
    assert outcome.ok is True
    assert outcome.payload["clocks"]


# ------------------------------------------------------------ tool specs


def test_every_tool_has_a_usable_schema():
    for name, spec in TOOLS_BY_NAME.items():
        assert spec.spec.description, f"{name} has no description"
        assert spec.spec.input_schema.get("type") == "object", f"{name} schema malformed"
        assert "properties" in spec.spec.input_schema, f"{name} declares no properties"


def test_every_tool_has_a_handler():
    ex = executor()
    for name in TOOLS_BY_NAME:
        assert hasattr(ex, f"_do_{name}"), f"{name} has no handler"


def test_every_tier_has_narration_guidance():
    """Without it a model reports the outcome ('the check succeeded') because
    that is what the tool result literally says."""
    from app.platform.ai.executor import _NARRATION_GUIDANCE
    from app.modules.rules.types import Tier

    for tier in Tier:
        assert tier.value in _NARRATION_GUIDANCE, f"no guidance for {tier.value}"


def test_roll_result_never_names_the_tier_to_the_narrator():
    """The dice are shown to the player separately; prose that repeats them
    reads like a spreadsheet."""
    from app.platform.ai.executor import _NARRATION_GUIDANCE

    banned = ("succeeded", "failed", "DC", "critical success", "partial success")
    for tier, text in _NARRATION_GUIDANCE.items():
        lowered = text.lower()
        for word in banned:
            assert word.lower() not in lowered, f"{tier} guidance says '{word}'"


# ------------------------------------------------------------ item provenance
#
# Session 2, turn 17: "I remember that I hold a spellbook of absolute death" -
# and the model granted it. Five player-invented artifacts later, one of them
# erased the town of Vareth. The gate under test: an item named by the player
# that canon has never heard of does not enter an inventory.


def _grant(ex, scope, item, **extra):
    return ex.execute(
        "give_item", {"actor_id": "pc-1", "item": item, **extra}, scope
    )


def gated(**overrides):
    """A scope with the provenance gate ON.

    It is off by default now - a GM who wants to hand themselves a dragon
    whistle should not have to argue with a regex - but the gate still exists
    for campaigns that want it, so it still has to be correct about what it
    detects.
    """
    return scope_with(character(), allow_player_grants=False, **overrides)


def test_player_invented_item_is_refused():
    scope = gated(
        player_text="I take out my one button atom bomb and nuke the city",
        established=("Borveld", "Vareth", "the Bent Axle"),
    )
    outcome = _grant(executor(), scope, "One button atom bomb")
    assert not outcome.ok
    assert "self-grant" in outcome.payload["error"]


def test_loot_from_a_canon_npc_is_granted():
    """"Borveld's bone-handled knife" names a canon NPC. Looting is the most
    common grant and always overlaps the player's own words - the gate must
    key on canon, not on overlap alone."""
    scope = gated(
        player_text="I take Borveld's knife off his body",
        established=("Borveld", "Vareth"),
    )
    outcome = _grant(executor(), scope, "Borveld's bone-handled knife")
    assert outcome.ok


def test_world_provided_item_without_player_mention_is_granted():
    scope = gated(player_text="I search the desk", established=())
    outcome = _grant(executor(), scope, "a sealed letter with dark wax")
    assert outcome.ok


def test_established_in_scene_overrides_the_gate():
    """The escape hatch exists for items the GM's own narration created this
    scene, before extraction has run. The override is recorded in the turn's
    tool calls, so abuse of it is visible in the export."""
    scope = gated(
        player_text="I pick up the iron key the jailer dropped", established=()
    )
    outcome = _grant(
        executor(), scope, "the jailer's iron key", established_in_scene=True
    )
    assert outcome.ok


def test_gate_is_inert_without_player_text():
    """Turns that never set player_text (older callers, open_scene) must not
    start refusing grants."""
    outcome = _grant(executor(), gated(), "a dented tin cup")
    assert outcome.ok


def test_grants_pass_freely_when_the_gate_is_off():
    """The default. Session 2's atom bomb would be granted now - deliberately,
    because the GM reviews what sticks and the model is told to make an
    outsized item cost something rather than refuse it."""
    scope = scope_with(
        character(),
        player_text="I take out my one button atom bomb",
        established=("Borveld",),
    )
    assert _grant(executor(), scope, "One button atom bomb").ok


# ------------------------------------------------------------ clock identity
#
# Session 2 held "nuke_detonation" (size 4, complete) and
# "clock:nuke-detonation" (size 6, empty) at once - the model re-created its
# own clock under a different spelling and every read after that was ambiguous.


def test_clock_recreation_under_a_different_spelling_is_rejected():
    existing = Clock(id="nuke_detonation", label="Detonation Countdown", size=4)
    scope = scope_with(character(), clocks=[existing])
    outcome = executor().execute(
        "create_clock",
        {"clock_id": "clock:nuke-detonation", "label": "Nuke goes off", "size": 6},
        scope,
    )
    assert not outcome.ok
    assert "nuke_detonation" in outcome.payload["hint"]


def test_clock_recreation_under_the_same_label_is_rejected():
    existing = Clock(id="pursuit", label="The Guards Close In", size=6)
    scope = scope_with(character(), clocks=[existing])
    outcome = executor().execute(
        "create_clock",
        {"clock_id": "guards_closing", "label": "the guards close in", "size": 4},
        scope,
    )
    assert not outcome.ok


def test_distinct_clocks_still_coexist():
    existing = Clock(id="pursuit", label="The Guards Close In", size=6)
    scope = scope_with(character(), clocks=[existing])
    outcome = executor().execute(
        "create_clock",
        {"clock_id": "ritual", "label": "The Ritual Completes", "size": 8},
        scope,
    )
    assert outcome.ok
