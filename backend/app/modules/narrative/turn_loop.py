"""The turn loop.

One turn is: player input -> model with tools -> engine validates and executes
-> model narrates the results -> persist. The model runs in a bounded tool-use
loop; it may call several tools before it has enough to narrate, but it cannot
loop forever and it cannot narrate an outcome it did not obtain from a tool.

Party handling: a turn names an actor, or names none and addresses the whole
party. Both paths run through the same loop with the same scope - the only
difference is who the model is told is acting. That is deliberate, because
solo-with-a-party and multiplayer-with-one-human are the same problem, and
building them as one thing means Milestone 5's networking is a transport
concern rather than a rewrite of game logic.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import UpstreamError
from app.modules.rules.engine import RulesEngine
from app.modules.rules.types import Character as RulesCharacter
from app.modules.rules.types import Clock, StateDelta
from app.platform.ai.context import ContextPacket
from app.platform.ai.executor import ToolExecutor, ToolOutcome, TurnScope
from app.platform.ai.gateway import Capability, CompletionRequest, Message
from app.platform.ai.prompts import render_prompt
from app.platform.ai.router import AIRouter
from app.platform.ai.tools import tool_specs
from app.platform.observability.metrics import turns_resolved

# A model that has not gathered enough to narrate after this many rounds of
# tool calls is confused, not thorough. Bounded so a bad turn costs a bounded
# amount of money.
MAX_TOOL_ROUNDS = 6

# Narration is prose for one turn, not a chapter. Capping it bounds both the
# bill and the blast radius of a degenerate repetition loop - a model that
# starts repeating a paragraph will otherwise fill the whole output window.
MAX_NARRATION_TOKENS = 900

# If the same paragraph comes back this many times the model is looping, not
# writing. Cheaper to truncate than to show the player a wall of it.
REPEAT_LIMIT = 2


def _strip_repetition(text: str) -> str:
    """Drop repeated paragraphs, keeping first occurrences in order.

    Degenerate repetition is a known failure mode under long contexts, and it
    is far more jarring in a game log than a slightly short scene - the player
    reads the same beat five times and loses trust in the whole thing.
    """
    seen: dict[str, int] = {}
    kept: list[str] = []
    for para in text.split("\n\n"):
        key = " ".join(para.split()).lower()[:160]
        if not key:
            continue
        seen[key] = seen.get(key, 0) + 1
        if seen[key] <= 1:
            kept.append(para.strip())
        elif seen[key] >= REPEAT_LIMIT:
            break
    return "\n\n".join(kept)


@dataclass
class TurnInput:
    session_id: str
    scene_id: str
    text: str
    # None means the input was addressed to the party as a whole.
    actor_id: str | None
    party: dict[str, RulesCharacter]
    clocks: dict[str, Clock] = field(default_factory=dict)
    context: ContextPacket = field(default_factory=ContextPacket)
    tone: str = "Grounded and grim, but not humourless."


@dataclass
class TurnOutcome:
    narration: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    deltas: dict[str, StateDelta] = field(default_factory=dict)
    clocks: dict[str, Clock] = field(default_factory=dict)
    party_after: dict[str, RulesCharacter] = field(default_factory=dict)
    prompt_version: str = ""
    rejected: int = 0


class TurnLoop:
    def __init__(self, ai: AIRouter, engine: RulesEngine) -> None:
        self._ai = ai
        self._engine = engine
        self._executor = ToolExecutor(engine)

    # ------------------------------------------------------------ prompting

    def _party_block(self, turn: TurnInput) -> str:
        """How the party is described to the model.

        Explicitly naming who is acting matters more than it looks: with four
        characters, an unqualified "you" is ambiguous, and a model resolving
        that ambiguity on its own will pick differently turn to turn.
        """
        lines: list[str] = []
        for character in turn.party.values():
            marker = ""
            if turn.actor_id and character.id == turn.actor_id:
                marker = "  <- acting this turn"
            lines.append(self._engine.describe(character) + marker)

        block = "\n\n".join(lines)

        if turn.actor_id is None:
            addressed = (
                "The player addressed this to the party as a whole. Narrate to "
                "the group. If the action needs one character to attempt it, "
                "pick whoever is most suited and say who is doing it."
            )
        else:
            acting = turn.party[turn.actor_id]
            addressed = (
                f"{acting.name} is acting this turn (actor_id: {turn.actor_id}). "
                f"Direct narration at {acting.name}. "
                "The rest of the party is present unless the fiction has "
                "established otherwise; they may react, but they do not act "
                "unless the player says so."
            )

        # Stated flatly because every rejection costs a full extra round trip,
        # and an invented id is the single most likely thing to be rejected.
        ids = ", ".join(f"{c.name}={cid}" for cid, c in turn.party.items())
        rule = (
            f"Valid actor_id values, and the only ones that exist: {ids}. "
            "Never invent or guess one."
        )

        return f"{block}\n\n{addressed}\n\n{rule}"

    def _system_prompt(self, turn: TurnInput) -> tuple[str, str]:
        return render_prompt(
            "gm_system",
            tone=turn.tone,
            context=turn.context.render() or "Nothing established yet.",
            party=self._party_block(turn),
        )

    def _scope(self, turn: TurnInput) -> TurnScope:
        return TurnScope(
            scene_id=turn.scene_id,
            characters=dict(turn.party),
            clocks=dict(turn.clocks),
        )

    # ------------------------------------------------------------ the loop

    async def run(self, turn: TurnInput) -> TurnOutcome:
        """Resolve a turn to completion. Returns everything the caller persists."""
        system, version = self._system_prompt(turn)
        scope = self._scope(turn)

        messages: list[Message] = [Message(role="user", content=turn.text)]
        recorded: list[dict[str, Any]] = []
        deltas: dict[str, StateDelta] = {}
        rejected = 0

        for _ in range(MAX_TOOL_ROUNDS):
            result = await self._ai.complete(
                CompletionRequest(
                    capability=Capability.RESOLVE_TURN,
                    system=system,
                    messages=messages,
                    tools=tool_specs(),
                    session_id=turn.session_id,
                    max_tokens=MAX_NARRATION_TOKENS,
                )
            )

            if not result.tool_calls:
                turns_resolved.labels(
                    play_mode="solo", outcome="ok"
                ).inc()
                return TurnOutcome(
                    narration=_strip_repetition(result.text),
                    tool_calls=recorded,
                    deltas=deltas,
                    clocks=scope.clocks,
                    party_after=scope.characters,
                    prompt_version=version,
                    rejected=rejected,
                )

            # Execute every call the model made, then hand all results back at
            # once. Batching keeps the round count - and the bill - down.
            tool_results: list[dict[str, Any]] = []
            for call in result.tool_calls:
                outcome = self._executor.execute(call.name, call.arguments, scope)
                if not outcome.ok:
                    rejected += 1
                else:
                    self._apply(outcome, scope, deltas)

                payload = outcome.to_tool_result()
                recorded.append({
                    "name": call.name,
                    "arguments": call.arguments,
                    "ok": outcome.ok,
                    "result": payload,
                })
                # `content` must be a string or a list of content blocks - a
                # bare object is rejected by the API. Serialising keeps the
                # structure legible to the model without inventing a schema.
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": json.dumps(payload),
                    # Marks a rejection as a failure rather than a result, so
                    # the model treats it as something to correct rather than
                    # something that happened.
                    "is_error": not outcome.ok,
                })

            messages = [
                *messages,
                Message(role="assistant", content=result.raw_content or result.text),
                Message(role="user", content=tool_results),
            ]

        turns_resolved.labels(play_mode="solo", outcome="tool_loop_exhausted").inc()
        raise UpstreamError(
            f"turn did not resolve within {MAX_TOOL_ROUNDS} rounds of tool calls"
        )

    def _apply(
        self, outcome: ToolOutcome, scope: TurnScope, deltas: dict[str, StateDelta]
    ) -> None:
        """Fold a tool's effect into the working scope.

        Applied to the in-memory scope immediately so subsequent tool calls in
        the same turn see current state - a model that damages a character then
        checks whether they are down must get the truth.
        """
        if outcome.clock is not None:
            scope.clocks[outcome.clock.id] = outcome.clock

        if outcome.delta is None or outcome.actor_id is None:
            return

        actor = scope.characters[outcome.actor_id]
        scope.characters[outcome.actor_id] = self._engine.ruleset.apply_delta(
            actor, outcome.delta
        )
        existing = deltas.get(outcome.actor_id)
        deltas[outcome.actor_id] = (
            outcome.delta if existing is None else existing.merge(outcome.delta)
        )

    async def open_scene(self, turn: TurnInput) -> str:
        """The first beat of a session.

        Without this a session starts on an empty screen and a blinking
        cursor, which asks the player to invent a scene the game master should
        have set. No tools: nothing has happened yet, so there is nothing to
        roll for.
        """
        system, _ = render_prompt(
            "open_scene",
            tone=turn.tone,
            premise=turn.text or "Not yet described.",
            party=self._party_block(turn),
            context=turn.context.render() or "Nothing established yet.",
        )
        result = await self._ai.complete(
            CompletionRequest(
                capability=Capability.NARRATE_SCENE,
                system=system,
                messages=[Message(role="user", content="Open the session.")],
                session_id=turn.session_id,
                max_tokens=MAX_NARRATION_TOKENS,
            )
        )
        return _strip_repetition(result.text)

    # ------------------------------------------------------------ streaming

    async def stream_narration(
        self, turn: TurnInput, outcome: TurnOutcome
    ) -> AsyncIterator[str]:
        """Re-narrate a resolved turn as a stream.

        Tool use and narration are separated on purpose. Streaming a response
        that is still deciding to call tools produces visible stalls mid
        sentence; resolving first and streaming the prose means the player sees
        smooth text from the first token.
        """
        system, _ = self._system_prompt(turn)
        summary = "\n".join(
            f"- {c['name']}: {c['result']}" for c in outcome.tool_calls if c["ok"]
        )
        prompt = (
            f"{turn.text}\n\n"
            f"[Resolved mechanics - narrate these, do not re-roll]\n{summary}"
            if summary else turn.text
        )
        async for chunk in self._ai.stream(
            CompletionRequest(
                capability=Capability.NARRATE_SCENE,
                system=system,
                messages=[Message(role="user", content=prompt)],
                session_id=turn.session_id,
            )
        ):
            yield chunk
