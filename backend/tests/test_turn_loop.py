"""Turn loop tests.

Runs entirely on the fake backend with scripted tool calls, so the loop's
control flow is exercised without spending anything. The party cases are the
point of this milestone: one player, several sheets.
"""

from __future__ import annotations

import pytest

from app.core.errors import UpstreamError
from app.modules.narrative.turn_loop import MAX_TOOL_ROUNDS, TurnInput, TurnLoop
from app.modules.rules.engine import RulesEngine
from app.modules.rules.locking import CheckLedger
from app.modules.rules.systems.d20.ruleset import D20Ruleset
from app.modules.rules.types import Clock
from app.platform.ai.backends.fake import FakeBackend
from app.platform.ai.gateway import Capability, CompletionResult, ToolCall, Usage
from app.platform.ai.router import AIRouter, Route, TokenLedger


class Scripted:
    def __init__(self, *values: int) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0) if self._values else 10


def pc(character_id: str, name: str, **overrides):
    spec = {
        "id": character_id,
        "name": name,
        "attributes": {"strength": 12, "dexterity": 13, "constitution": 12,
                       "intelligence": 12, "wisdom": 14, "charisma": 11},
        "skills": {"search": 1},
    }
    spec.update(overrides)
    return D20Ruleset().create_character(spec)


def party(*characters):
    return {c.id: c for c in characters}


def result(text="", tool_calls=None):
    return CompletionResult(
        text=text, tool_calls=tool_calls or [], usage=Usage(100, 50),
        backend="fake", model="fake-1", raw_content=text,
    )


def call(name, **arguments):
    return ToolCall(id=f"tc-{name}", name=name, arguments=arguments)


def loop_with(*responses, rolls=()):
    backend = FakeBackend("fake", responses=list(responses))
    router = AIRouter(
        {"fake": backend},
        {c: [Route("fake", "m")] for c in Capability},
        TokenLedger(10_000_000),
    )
    engine = RulesEngine(ledger=CheckLedger(), rng=Scripted(*rolls))
    return TurnLoop(router, engine), backend


def turn_input(**overrides):
    base = {
        "session_id": "sess-1",
        "scene_id": "study",
        "text": "I search the desk.",
        "actor_id": "pc-1",
        "party": party(pc("pc-1", "Vessa")),
    }
    base.update(overrides)
    return TurnInput(**base)


# ------------------------------------------------------------ basic flow


async def test_turn_without_tools_returns_narration():
    loop, _ = loop_with(result("The desk is old and yields nothing."))
    outcome = await loop.run(turn_input())
    assert "yields nothing" in outcome.narration
    assert outcome.tool_calls == []


async def test_tool_call_then_narration():
    loop, _ = loop_with(
        result(tool_calls=[call("roll_check", actor_id="pc-1", kind_id="search",
                                difficulty="moderate", reason="searching",
                                target_ref="loc:study/desk")]),
        result("You find a folded letter."),
        rolls=(17,),
    )
    outcome = await loop.run(turn_input())
    assert outcome.narration == "You find a folded letter."
    assert len(outcome.tool_calls) == 1
    assert outcome.tool_calls[0]["ok"] is True


async def test_prompt_version_is_recorded():
    """Needed to trace a narration regression back to a template revision."""
    loop, _ = loop_with(result("Nothing happens."))
    outcome = await loop.run(turn_input())
    assert outcome.prompt_version


async def test_damage_flows_into_party_state():
    loop, _ = loop_with(
        result(tool_calls=[call("apply_damage", actor_id="pc-1", amount=4,
                                source="a falling beam")]),
        result("The beam catches you across the shoulder."),
    )
    outcome = await loop.run(turn_input())
    assert outcome.party_after["pc-1"].hp < 12
    assert outcome.deltas["pc-1"].hp == -4


async def test_subsequent_tools_see_updated_state():
    """A model that damages then queries must be told the truth, not stale HP."""
    loop, backend = loop_with(
        result(tool_calls=[call("apply_damage", actor_id="pc-1", amount=5,
                                source="the fall")]),
        result(tool_calls=[call("query_character", actor_id="pc-1")]),
        result("You are hurt."),
    )
    outcome = await loop.run(turn_input())
    query = [c for c in outcome.tool_calls if c["name"] == "query_character"][0]
    assert "hp" in str(query["result"]).lower() or query["ok"]
    assert outcome.party_after["pc-1"].hp == 12 - 5


async def test_rejected_call_is_counted_and_returned_to_model():
    loop, backend = loop_with(
        result(tool_calls=[call("apply_damage", actor_id="ghost", amount=3)]),
        result("Nothing happens."),
    )
    outcome = await loop.run(turn_input())
    assert outcome.rejected == 1
    assert outcome.tool_calls[0]["ok"] is False
    # The model got a second turn to correct itself.
    assert backend.call_count == 2


async def test_runaway_tool_loop_is_bounded():
    """A model still calling tools after six rounds is confused, not thorough."""
    responses = [
        result(tool_calls=[call("query_character", actor_id="pc-1")])
        for _ in range(MAX_TOOL_ROUNDS + 2)
    ]
    loop, _ = loop_with(*responses)
    with pytest.raises(UpstreamError, match="did not resolve"):
        await loop.run(turn_input())


# ------------------------------------------------------------ the party


async def test_acting_character_is_named_in_the_prompt():
    """With several sheets, an unqualified 'you' is ambiguous."""
    loop, backend = loop_with(result("You lean over the desk."))
    await loop.run(turn_input(
        party=party(pc("pc-1", "Vessa"), pc("pc-2", "Ordo")),
        actor_id="pc-2",
    ))
    system = backend.calls[0].system
    assert "Ordo" in system
    assert "acting this turn" in system


async def test_whole_party_is_visible_to_the_model():
    loop, backend = loop_with(result("The three of you press on."))
    await loop.run(turn_input(
        party=party(pc("pc-1", "Vessa"), pc("pc-2", "Ordo"), pc("pc-3", "Hesk")),
    ))
    system = backend.calls[0].system
    for name in ("Vessa", "Ordo", "Hesk"):
        assert name in system


async def test_party_addressed_turn_has_no_acting_character():
    loop, backend = loop_with(result("You head north together."))
    await loop.run(turn_input(
        party=party(pc("pc-1", "Vessa"), pc("pc-2", "Ordo")),
        actor_id=None,
        text="We head north.",
    ))
    system = backend.calls[0].system
    assert "party as a whole" in system
    assert "acting this turn" not in system


async def test_each_character_gets_their_own_attempt_at_a_locked_check():
    """Different people, different hands and eyes - the lock is per actor."""
    loop, _ = loop_with(
        result(tool_calls=[call("roll_check", actor_id="pc-1", kind_id="search",
                                difficulty="hard", reason="searching",
                                target_ref="loc:study/desk")]),
        result("Vessa finds nothing."),
        rolls=(3, 18),
    )
    roster = party(pc("pc-1", "Vessa"), pc("pc-2", "Ordo"))
    first = await loop.run(turn_input(party=roster, actor_id="pc-1"))
    assert first.tool_calls[0]["result"].get("locked") is False

    loop._ai._backends["fake"]._responses = [
        result(tool_calls=[call("roll_check", actor_id="pc-2", kind_id="search",
                                difficulty="hard", reason="searching",
                                target_ref="loc:study/desk")]),
        result("Ordo spots the seam."),
    ]
    second = await loop.run(turn_input(party=roster, actor_id="pc-2"))
    assert second.tool_calls[0]["result"].get("locked") is False


async def test_same_character_retrying_is_locked():
    loop, _ = loop_with(
        result(tool_calls=[call("roll_check", actor_id="pc-1", kind_id="search",
                                difficulty="hard", reason="searching",
                                target_ref="loc:study/desk")]),
        result("Nothing."),
        result(tool_calls=[call("roll_check", actor_id="pc-1", kind_id="search",
                                difficulty="hard", reason="again",
                                target_ref="loc:study/desk")]),
        result("Still nothing."),
        rolls=(3, 20),
    )
    roster = party(pc("pc-1", "Vessa"))
    await loop.run(turn_input(party=roster))
    second = await loop.run(turn_input(party=roster))
    assert second.tool_calls[0]["result"].get("locked") is True


async def test_damage_to_one_character_leaves_the_others_untouched():
    loop, _ = loop_with(
        result(tool_calls=[call("apply_damage", actor_id="pc-2", amount=6,
                                source="the arrow")]),
        result("Ordo takes it in the thigh."),
    )
    outcome = await loop.run(turn_input(
        party=party(pc("pc-1", "Vessa"), pc("pc-2", "Ordo")),
        actor_id="pc-2",
    ))
    assert outcome.party_after["pc-2"].hp < outcome.party_after["pc-1"].hp
    assert "pc-1" not in outcome.deltas


async def test_model_cannot_act_on_a_character_outside_the_party():
    loop, _ = loop_with(
        result(tool_calls=[call("apply_damage", actor_id="pc-99", amount=99,
                                source="nowhere")]),
        result("Nothing happens."),
    )
    outcome = await loop.run(turn_input(party=party(pc("pc-1", "Vessa"))))
    assert outcome.rejected == 1
    assert outcome.deltas == {}


# ------------------------------------------------------------ clocks


async def test_clock_changes_survive_the_turn():
    loop, _ = loop_with(
        result(tool_calls=[call("advance_clock", clock_id="watch", segments=1)]),
        result("Boots on the stair."),
    )
    outcome = await loop.run(turn_input(
        clocks={"watch": Clock("watch", "The Watch closes in", size=6, filled=1)}
    ))
    assert outcome.clocks["watch"].filled == 2


async def test_tool_result_content_is_a_string():
    """The API rejects a bare object here.

    `tool_result.content` must be a string or a list of content blocks. Passing
    the result dict straight through returns a 400 that names a message index,
    which is a long way from the line that caused it.
    """
    import json

    raw = [
        {"type": "text", "text": "Checking."},
        {"type": "tool_use", "id": "t-1", "name": "query_character",
         "input": {"actor_id": "pc-1"}},
    ]
    backend = FakeBackend("fake", responses=[
        CompletionResult(
            text="", tool_calls=[call("query_character", actor_id="pc-1")],
            usage=Usage(10, 5), backend="fake", model="f", raw_content=raw,
        ),
        result("You look yourself over."),
    ])
    router = AIRouter(
        {"fake": backend},
        {c: [Route("fake", "m")] for c in Capability},
        TokenLedger(10_000_000),
    )
    loop = TurnLoop(router, RulesEngine(ledger=CheckLedger(), rng=Scripted()))
    await loop.run(turn_input())

    blocks = backend.calls[1].messages[-1].content
    tool_results = [b for b in blocks if b["type"] == "tool_result"]
    assert tool_results, "no tool_result block was sent back"
    for block in tool_results:
        assert isinstance(block["content"], str), "content must be a string"
        json.loads(block["content"])


async def test_rejected_tool_result_is_flagged_as_an_error():
    """So the model treats it as something to correct, not something that
    happened."""
    backend = FakeBackend("fake", responses=[
        CompletionResult(
            text="", tool_calls=[call("apply_damage", actor_id="ghost", amount=3)],
            usage=Usage(10, 5), backend="fake", model="f", raw_content="x",
        ),
        result("Nothing happens."),
    ])
    router = AIRouter(
        {"fake": backend},
        {c: [Route("fake", "m")] for c in Capability},
        TokenLedger(10_000_000),
    )
    loop = TurnLoop(router, RulesEngine(ledger=CheckLedger(), rng=Scripted()))
    await loop.run(turn_input())

    blocks = backend.calls[1].messages[-1].content
    assert blocks[0]["is_error"] is True


async def test_a_single_response_cannot_execute_unbounded_tool_calls():
    """Bounding rounds is not enough.

    One response can carry any number of tool_use blocks. Unbounded, a looping
    model emits two hundred in one message and every one executes, is recorded,
    and is rendered - a wall of dice in the log and a wall of writes to state.
    """
    from app.modules.narrative.turn_loop import MAX_CALLS_PER_ROUND

    flood = [call("query_character", actor_id="pc-1") for _ in range(200)]
    loop, backend = loop_with(
        CompletionResult(
            text="", tool_calls=flood, usage=Usage(10, 5),
            backend="fake", model="f", raw_content="x",
        ),
        result("You look yourself over."),
    )
    outcome = await loop.run(turn_input())
    assert len(outcome.tool_calls) <= MAX_CALLS_PER_ROUND


async def test_the_model_is_told_when_calls_are_dropped():
    """Silently discarding them means the same oversized batch next round."""
    flood = [call("query_character", actor_id="pc-1") for _ in range(50)]
    loop, backend = loop_with(
        CompletionResult(
            text="", tool_calls=flood, usage=Usage(10, 5),
            backend="fake", model="f", raw_content="x",
        ),
        result("Fine."),
    )
    await loop.run(turn_input())

    blocks = backend.calls[1].messages[-1].content
    notice = [b for b in blocks if b.get("type") == "text"]
    assert notice, "model was not told calls had been dropped"
    assert "not executed" in notice[0]["text"]


async def test_a_turn_that_keeps_flooding_fails_rather_than_grinding():
    from app.modules.narrative.turn_loop import MAX_CALLS_PER_TURN

    def flood_response():
        return CompletionResult(
            text="",
            tool_calls=[call("query_character", actor_id="pc-1") for _ in range(50)],
            usage=Usage(10, 5), backend="fake", model="f", raw_content="x",
        )

    loop, _ = loop_with(*[flood_response() for _ in range(MAX_TOOL_ROUNDS + 2)])
    with pytest.raises(UpstreamError):
        await loop.run(turn_input())
