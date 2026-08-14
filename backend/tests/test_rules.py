"""Rules engine tests.

Heavy on boundary conditions, because off-by-one errors in the tier ladder are
exactly the class of bug that stays invisible until session six.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core.errors import InvalidRequest
from app.modules.rules.dice import SeededRng, roll, roll_d20
from app.modules.rules.engine import RulesEngine
from app.modules.rules.locking import CheckLedger, Situation
from app.modules.rules.systems.d20.ruleset import D20Ruleset
from app.modules.rules.types import (
    ActiveCondition,
    Character,
    CheckRequest,
    Clock,
    RollMode,
    StateDelta,
    Tier,
)


class FixedRng:
    """Returns a scripted sequence of die faces."""

    def __init__(self, *values: int) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0) if self._values else b


def make_character(**overrides) -> Character:
    base = {
        "id": "pc-1",
        "name": "Test Subject",
        "attributes": {
            "strength": 12, "dexterity": 14, "constitution": 12,
            "intelligence": 10, "wisdom": 14, "charisma": 10,
        },
        "skills": {"search": 2},
        "hp": 12,
        "hp_max": 12,
    }
    base.update(overrides)
    return Character(**base)


# ------------------------------------------------------------------ dice


def test_notation_parses_count_sides_modifier():
    result = roll("2d6+3", FixedRng(4, 5))
    assert result.rolls == (4, 5)
    assert result.total == 12


def test_keep_highest_discards_lowest():
    result = roll("4d6kh3", FixedRng(6, 1, 5, 4))
    assert result.kept == (6, 5, 4)
    assert result.total == 15


def test_keep_lowest():
    result = roll("2d20kl1", FixedRng(18, 3))
    assert result.kept == (3,)


@pytest.mark.parametrize("bad", ["", "d", "2x6", "0d6", "200d6", "1d1", "2d6+"])
def test_invalid_notation_rejected(bad):
    with pytest.raises(InvalidRequest):
        roll(bad, SeededRng(1))


def test_advantage_natural_reads_the_kept_die():
    """If natural read both dice, advantage would double the crit rate."""
    result = roll_d20(FixedRng(20, 3), mode=RollMode.DISADVANTAGE)
    assert result.rolls == (20, 3)
    assert result.kept == (3,)
    assert result.natural == 3, "disadvantage must not inherit the discarded 20"


def test_advantage_keeps_higher():
    result = roll_d20(FixedRng(7, 19), mode=RollMode.ADVANTAGE)
    assert result.natural == 19


def test_seeded_rng_is_reproducible():
    a = [roll("3d6", SeededRng(42)).total for _ in range(3)]
    b = [roll("3d6", SeededRng(42)).total for _ in range(3)]
    assert a == b


# ------------------------------------------------------------------ tiers


@pytest.mark.parametrize(
    "die,dc,expected",
    [
        # modifier is +4 (insight 14 -> +2, search skill +2)
        (19, 13, Tier.CRIT_SUCCESS),   # margin +10
        (18, 13, Tier.SUCCESS),        # margin +9
        (9, 13, Tier.SUCCESS),         # margin 0
        (8, 13, Tier.PARTIAL),         # margin -1
        (5, 13, Tier.PARTIAL),         # margin -4
        (4, 13, Tier.FAILURE),         # margin -5
        (2, 13, Tier.FAILURE),         # margin -7
        (10, 24, Tier.CRIT_FAILURE),   # margin -10
    ],
)
def test_tier_boundaries(die, dc, expected):
    ruleset = D20Ruleset()
    actor = make_character()
    request = CheckRequest(actor_id=actor.id, kind_id="search", dc=dc)
    result = ruleset.resolve_check(actor, request, FixedRng(die))
    assert result.tier is expected, f"die {die} vs dc {dc} -> margin {result.margin}"


def test_natural_20_is_absolute_regardless_of_margin():
    """A nat 20 crits even when the arithmetic says failure."""
    ruleset = D20Ruleset()
    actor = make_character()
    # dc 24, modifier +4: total 24, margin 0 - would be a plain success.
    request = CheckRequest(actor_id=actor.id, kind_id="search", dc=24)
    result = ruleset.resolve_check(actor, request, FixedRng(20))
    assert result.tier is Tier.CRIT_SUCCESS
    assert result.override is True


def test_natural_1_is_absolute_regardless_of_margin():
    ruleset = D20Ruleset()
    actor = make_character()
    request = CheckRequest(actor_id=actor.id, kind_id="search", dc=1)
    result = ruleset.resolve_check(actor, request, FixedRng(1))
    assert result.tier is Tier.CRIT_FAILURE
    assert result.override is True


def test_crit_success_always_carries_a_boon():
    ruleset = D20Ruleset()
    actor = make_character()
    request = CheckRequest(actor_id=actor.id, kind_id="search", dc=12)
    result = ruleset.resolve_check(actor, request, FixedRng(20))
    assert result.boon is not None
    assert result.setback is None
    assert result.boon.hint


def test_crit_failure_always_carries_a_setback():
    ruleset = D20Ruleset()
    actor = make_character()
    request = CheckRequest(actor_id=actor.id, kind_id="search", dc=12)
    result = ruleset.resolve_check(actor, request, FixedRng(1))
    assert result.setback is not None
    assert result.boon is None


def test_boon_scale_tracks_difficulty():
    """Critting a trivial check must not hand out a relic."""
    ruleset = D20Ruleset()
    actor = make_character()
    trivial = ruleset.resolve_check(
        actor, CheckRequest(actor.id, "search", dc=8), FixedRng(20)
    )
    hard = ruleset.resolve_check(
        actor, CheckRequest(actor.id, "search", dc=22), FixedRng(20)
    )
    assert trivial.boon.scale.value == "minor"
    assert hard.boon.scale.value == "major"


@given(
    die=st.integers(min_value=2, max_value=19),
    dc=st.integers(min_value=5, max_value=25),
)
def test_tier_is_monotonic_in_die_face(die, dc):
    """Property: a higher die never produces a worse tier, criticals aside."""
    ruleset = D20Ruleset()
    actor = make_character()
    lower = ruleset.resolve_check(actor, CheckRequest(actor.id, "search", dc), FixedRng(die))
    higher = ruleset.resolve_check(
        actor, CheckRequest(actor.id, "search", dc), FixedRng(die + 1)
    )
    if lower.rolled and higher.rolled:
        assert higher.tier.rank >= lower.tier.rank


@given(die=st.integers(min_value=1, max_value=20), dc=st.integers(min_value=1, max_value=30))
def test_every_roll_produces_a_valid_tier(die, dc):
    ruleset = D20Ruleset()
    actor = make_character()
    result = ruleset.resolve_check(actor, CheckRequest(actor.id, "search", dc), FixedRng(die))
    assert isinstance(result.tier, Tier)
    assert (result.boon is None) or (result.setback is None)


# ------------------------------------------------------------------ roll gate


def test_impossible_task_is_never_rolled():
    """Absolute crits make this load-bearing: without it, patience beats any DC."""
    ruleset = D20Ruleset()
    actor = make_character()
    request = CheckRequest(actor_id=actor.id, kind_id="search", dc=40)
    should_roll, reason = ruleset.roll_required(actor, request)
    assert should_roll is False
    assert "beyond" in reason

    result = ruleset.resolve_check(actor, request, FixedRng(20))
    assert result.tier is Tier.FAILURE, "a nat 20 must not rescue an impossible task"
    assert result.rolled is False


def test_trivial_task_is_auto_passed():
    ruleset = D20Ruleset()
    actor = make_character()
    request = CheckRequest(actor_id=actor.id, kind_id="search", dc=-8)
    result = ruleset.resolve_check(actor, request, FixedRng(1))
    assert result.tier is Tier.SUCCESS, "a nat 1 must not fail a trivial task"
    assert result.rolled is False


def test_blocked_action_is_not_rolled():
    ruleset = D20Ruleset()
    actor = make_character(conditions=(ActiveCondition("blinded"),))
    should_roll, reason = ruleset.roll_required(
        actor, CheckRequest(actor.id, "search", dc=12)
    )
    assert should_roll is False
    assert "blocked" in reason


# ------------------------------------------------------------------ conditions


def test_conditions_modify_checks():
    ruleset = D20Ruleset()
    clean = make_character()
    poisoned = make_character(conditions=(ActiveCondition("poisoned"),))
    assert ruleset.total_modifier(poisoned, "search") == (
        ruleset.total_modifier(clean, "search") - 2
    )


def test_condition_only_affects_listed_attributes():
    ruleset = D20Ruleset()
    frightened = make_character(conditions=(ActiveCondition("frightened"),))
    # frightened hits charisma and intelligence, not wisdom
    assert ruleset.total_modifier(frightened, "search") == ruleset.total_modifier(
        make_character(), "search"
    )
    assert ruleset.total_modifier(frightened, "persuade") < ruleset.total_modifier(
        make_character(), "persuade"
    )


def test_conditions_expire_after_their_duration():
    condition = ActiveCondition("poisoned", remaining_ticks=2)
    once = condition.tick()
    assert once.remaining_ticks == 1
    assert once.tick() is None


def test_indefinite_conditions_never_expire():
    condition = ActiveCondition("bleeding", remaining_ticks=None)
    assert condition.tick() is condition


def test_tick_applies_damage_and_expiry():
    ruleset = D20Ruleset()
    actor = make_character(conditions=(ActiveCondition("bleeding", remaining_ticks=1),))
    delta = ruleset.tick_conditions(actor)
    assert delta.hp == -1
    assert "bleeding" in delta.remove_conditions


def test_unknown_condition_rejected():
    ruleset = D20Ruleset()
    with pytest.raises(InvalidRequest):
        ruleset.apply_delta(
            make_character(), StateDelta(add_conditions=(ActiveCondition("cursed"),))
        )


# ------------------------------------------------------------------ deltas


def test_hp_clamps_at_zero_and_max():
    ruleset = D20Ruleset()
    actor = make_character(hp=3, hp_max=12)
    assert ruleset.apply_delta(actor, StateDelta(hp=-99)).hp == 0
    assert ruleset.apply_delta(actor, StateDelta(hp=99)).hp == 12


def test_apply_delta_does_not_mutate():
    ruleset = D20Ruleset()
    actor = make_character(hp=10)
    ruleset.apply_delta(actor, StateDelta(hp=-5))
    assert actor.hp == 10, "rules must never mutate; they return deltas"


def test_deltas_merge():
    merged = StateDelta(hp=-2, add_items=("rope",)).merge(
        StateDelta(hp=-1, add_items=("torch",))
    )
    assert merged.hp == -3
    assert merged.add_items == ("rope", "torch")


def test_partial_success_has_no_automatic_mechanical_cost():
    """A partial costs something *concrete* that the narrator names - noise,
    time, a broken tool. A generic counter ticking up was less interesting and
    easy for the player to ignore."""
    ruleset = D20Ruleset()
    actor = make_character()
    result = ruleset.resolve_check(
        actor, CheckRequest(actor.id, "search", dc=13), FixedRng(8)
    )
    assert result.tier is Tier.PARTIAL
    delta = ruleset.apply_consequence(actor, result)
    assert delta.hp == 0
    # The obligation is carried as a note for the narrator, not as damage.
    assert any("cost" in n for n in delta.notes)


def test_stress_is_gone():
    """Removed as a mechanic. Nothing should still reference it."""
    from app.modules.rules.types import Character, StateDelta

    assert not hasattr(Character(id="x", name="X", attributes={}), "stress")
    assert "stress" not in StateDelta.__dataclass_fields__


# ------------------------------------------------------------------ clocks


def test_clock_advances_and_completes():
    clock = Clock("pursuit", "The Watch closes in", size=4)
    assert not clock.complete
    assert clock.advance(4).complete


def test_clock_cannot_overfill_or_underflow():
    clock = Clock("c", "c", size=4, filled=3)
    assert clock.advance(10).filled == 4
    assert clock.reduce(10).filled == 0


def test_clock_rejects_invalid_construction():
    with pytest.raises(ValueError):
        Clock("c", "c", size=0)
    with pytest.raises(ValueError):
        Clock("c", "c", size=4, filled=5)


# ------------------------------------------------------------------ locking


def _situation(**overrides) -> Situation:
    base = {"scene_id": "scene-1", "condition_ids": (), "relevant_assets": ()}
    base.update(overrides)
    return Situation(**base)


def test_repeated_check_returns_the_stored_result():
    """The core exploit: retry until a nat 20."""
    engine = RulesEngine(ledger=CheckLedger(), rng=FixedRng(3, 20, 20, 20))
    actor = make_character()
    request = CheckRequest(actor.id, "search", dc=15, target_ref="loc:study/desk")

    first = engine.resolve(actor, request, _situation())
    assert first.locked is False

    for _ in range(3):
        repeat = engine.resolve(actor, request, _situation())
        assert repeat.locked is True
        assert repeat.result.tier is first.result.tier
        assert repeat.result.reason is not None


def test_lock_reopens_when_conditions_change():
    engine = RulesEngine(ledger=CheckLedger(), rng=FixedRng(3, 20))
    actor = make_character()
    request = CheckRequest(actor.id, "search", dc=15, target_ref="loc:study/desk")

    engine.resolve(actor, request, _situation())
    reopened = engine.resolve(actor, request, _situation(condition_ids=("inspired",)))
    assert reopened.locked is False


def test_lock_reopens_with_a_new_relevant_asset():
    engine = RulesEngine(ledger=CheckLedger(), rng=FixedRng(3, 20))
    actor = make_character()
    request = CheckRequest(actor.id, "search", dc=15, target_ref="loc:study/desk")

    engine.resolve(actor, request, _situation())
    reopened = engine.resolve(actor, request, _situation(relevant_assets=("lantern",)))
    assert reopened.locked is False


def test_different_actor_gets_their_own_attempt():
    engine = RulesEngine(ledger=CheckLedger(), rng=FixedRng(3, 17))
    request_a = CheckRequest("pc-1", "search", dc=15, target_ref="loc:study/desk")
    request_b = CheckRequest("pc-2", "search", dc=15, target_ref="loc:study/desk")

    engine.resolve(make_character(id="pc-1"), request_a, _situation())
    second = engine.resolve(make_character(id="pc-2"), request_b, _situation())
    assert second.locked is False


def test_pushed_check_bypasses_the_lock():
    """Spending a resource is the one retry that should always be allowed."""
    engine = RulesEngine(ledger=CheckLedger(), rng=FixedRng(3, 18))
    actor = make_character()
    target = "loc:study/desk"

    engine.resolve(actor, CheckRequest(actor.id, "search", 15, target), _situation())
    pushed = engine.resolve(
        actor, CheckRequest(actor.id, "search", 15, target, pushed=True), _situation()
    )
    assert pushed.locked is False


def test_attacks_never_lock():
    engine = RulesEngine(ledger=CheckLedger(), rng=FixedRng(5, 5, 5))
    actor = make_character()
    request = CheckRequest(actor.id, "strike", dc=12, target_ref="npc:guard-1")
    for _ in range(3):
        assert engine.resolve(actor, request, _situation()).locked is False


def test_recall_lore_locks_across_scenes():
    """You know it or you do not; a new room does not change that."""
    engine = RulesEngine(ledger=CheckLedger(), rng=FixedRng(4, 20))
    actor = make_character()
    request = CheckRequest(actor.id, "recall", dc=15, target_ref="topic:the-duke")

    engine.resolve(actor, request, _situation(scene_id="scene-1"))
    elsewhere = engine.resolve(actor, request, _situation(scene_id="scene-2"))
    assert elsewhere.locked is True


def test_per_scene_lock_releases_in_a_new_scene():
    engine = RulesEngine(ledger=CheckLedger(), rng=FixedRng(4, 12))
    actor = make_character()
    request = CheckRequest(actor.id, "persuade", dc=14, target_ref="npc:innkeeper")

    engine.resolve(actor, request, _situation(scene_id="scene-1"))
    later = engine.resolve(actor, request, _situation(scene_id="scene-2"))
    assert later.locked is False


def test_check_without_target_ref_does_not_lock():
    engine = RulesEngine(ledger=CheckLedger(), rng=FixedRng(4, 4))
    actor = make_character()
    request = CheckRequest(actor.id, "search", dc=12, target_ref=None)
    assert engine.resolve(actor, request, _situation()).locked is False


# ------------------------------------------------------------------ characters


def test_attribute_modifier_curve():
    actor = make_character(
        attributes={"strength": 3, "dexterity": 10, "constitution": 14, "intelligence": 18,
                    "wisdom": 11, "charisma": 8}
    )
    assert actor.attribute_mod("strength") == -4
    assert actor.attribute_mod("dexterity") == 0
    assert actor.attribute_mod("constitution") == 2
    assert actor.attribute_mod("intelligence") == 4
    assert actor.attribute_mod("charisma") == -1


def test_create_character_validates_attributes():
    ruleset = D20Ruleset()
    with pytest.raises(InvalidRequest):
        ruleset.create_character({"id": "x", "name": "X", "attributes": {"strength": 12}})
    with pytest.raises(InvalidRequest):
        ruleset.create_character({
            "id": "x", "name": "X",
            "attributes": {a: 99 for a in
                           ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")},
        })


def test_create_character_derives_hp_from_endurance():
    ruleset = D20Ruleset()
    tough = ruleset.create_character({
        "id": "x", "name": "X",
        "attributes": {"strength": 8, "dexterity": 8, "constitution": 15,
                       "intelligence": 10, "wisdom": 10, "charisma": 8},
    })
    assert tough.hp_max == 14
    assert tough.hp == tough.hp_max


def test_point_buy_is_enforced_at_creation():
    """A client is not a validator - nothing stops a crafted request."""
    ruleset = D20Ruleset()
    with pytest.raises(InvalidRequest, match="over budget"):
        ruleset.create_character({
            "id": "x", "name": "X",
            "attributes": dict.fromkeys(
                ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"), 15
            ),
        })


def test_skill_points_are_enforced():
    ruleset = D20Ruleset()
    with pytest.raises(InvalidRequest, match="skill points"):
        ruleset.create_character({
            "id": "x", "name": "X",
            "attributes": {"strength": 10, "dexterity": 10, "constitution": 10,
                           "intelligence": 10, "wisdom": 10, "charisma": 10},
            "skills": {"search": 3, "sneak": 3, "perceive": 3},
        })


def test_build_can_be_bypassed_for_generated_characters():
    """NPCs and imported statblocks do not go through point buy."""
    ruleset = D20Ruleset()
    npc = ruleset.create_character({
        "id": "npc", "name": "Ogre", "enforce_build": False,
        "attributes": {"strength": 18, "dexterity": 8, "constitution": 17,
                       "intelligence": 6, "wisdom": 8, "charisma": 12},
    })
    assert npc.attribute_mod("strength") == 4


def test_describe_for_model_includes_state():
    ruleset = D20Ruleset()
    actor = make_character(
        conditions=(ActiveCondition("poisoned", remaining_ticks=3),),
        inventory=("rope", "lantern"),
    )
    described = ruleset.describe_for_model(actor)
    assert "HP 12/12" in described
    assert "Poisoned (3)" in described
    assert "rope" in described


# ------------------------------------------------------------------ registry


def test_registry_exposes_the_primary_ruleset():
    from app.modules.rules import registry

    assert registry.get("d20").id == "d20"
    assert len(registry.available()) >= 1


def test_unknown_ruleset_rejected():
    from app.modules.rules import registry

    with pytest.raises(InvalidRequest):
        registry.get("nonexistent")


def test_ruleset_satisfies_the_protocol():
    from app.modules.rules.protocol import Ruleset

    assert isinstance(D20Ruleset(), Ruleset)


def test_every_check_kind_uses_a_real_attribute():
    from app.modules.rules.systems.d20.ruleset import ATTRIBUTES

    for kind in D20Ruleset().check_kinds():
        assert kind.attribute in ATTRIBUTES


def test_no_condition_targets_an_attribute_that_does_not_exist():
    """A typo here fails silently - the condition simply never applies, and a
    blinded character quietly keeps their full perception."""
    from app.modules.rules.systems.d20.ruleset import ATTRIBUTES, CONDITIONS

    for spec in CONDITIONS:
        for attribute in spec.affects_attributes:
            assert attribute in ATTRIBUTES, (
                f"condition '{spec.id}' targets unknown attribute '{attribute}'"
            )


def test_no_condition_blocks_an_action_that_does_not_exist():
    from app.modules.rules.systems.d20.ruleset import CHECK_KINDS, CONDITIONS

    known = {k.id for k in CHECK_KINDS}
    for spec in CONDITIONS:
        for action in spec.blocks_actions:
            assert action in known, (
                f"condition '{spec.id}' blocks unknown action '{action}'"
            )


def test_legacy_attribute_names_are_migrated_on_load():
    """Sheets written before the rename still load."""
    ruleset = D20Ruleset()
    old = ruleset.create_character({
        "id": "x", "name": "X",
        "attributes": {"might": 12, "agility": 13, "endurance": 12,
                       "wits": 11, "insight": 12, "presence": 10},
    })
    assert old.attributes["strength"] == 12
    assert old.attribute_mod("constitution") == 1


def test_build_rules_costs_are_json_indexable():
    """JSON has no integer keys.

    A client indexing costs with String(score) against an int-keyed map misses
    every entry, computes a spend of zero, and silently disables the create
    button - which looks like the button being broken rather than the contract
    being wrong.
    """
    import json

    from app.modules.rules.systems.d20.ruleset import POINT_BUDGET

    rules = D20Ruleset().build_rules()
    costs = json.loads(json.dumps(rules))["costs"]

    assert all(isinstance(k, str) for k in costs)
    for score in range(rules["min"], rules["max"] + 1):
        assert str(score) in costs, f"no cost published for score {score}"

    # A legal spread must total exactly the budget, or the builder can never
    # reach a completable state.
    legal = [15, 15, 11, 10, 10, 10]
    assert sum(costs[str(v)] for v in legal) == POINT_BUDGET
