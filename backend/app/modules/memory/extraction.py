"""Extraction: turning narration into durable rows.

Everything the model says becomes data, or the campaign has no memory. This
runs after a turn resolves, on a cheap model, against the narration only.

Deliberately conservative. A false entity ("the cold air") is worse than a
missed one: it pollutes retrieval, wastes budget, and shows up in the Codex
looking like a mistake. Misses get caught the next time the thing is mentioned.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.platform.ai.gateway import Capability, CompletionRequest, Message
from app.platform.ai.prompts import render_prompt
from app.platform.ai.router import AIRouter

MAX_ENTITIES_PER_TURN = 6
MAX_FACTS_PER_TURN = 6


class ExtractedEntity(BaseModel):
    kind: str = Field(description="npc | location | faction | item | creature | concept")
    name: str = Field(max_length=200)
    summary: str = Field(default="", max_length=400)


class ExtractedFact(BaseModel):
    subject: str = Field(max_length=200)
    predicate: str = Field(max_length=120)
    object: str = Field(max_length=400)


class Extraction(BaseModel):
    entities: list[ExtractedEntity] = Field(default_factory=list)
    facts: list[ExtractedFact] = Field(default_factory=list)


class Extractor:
    def __init__(self, ai: AIRouter) -> None:
        self._ai = ai

    async def extract(
        self,
        narration: str,
        *,
        session_id: str,
        known: list[str] | None = None,
    ) -> Extraction:
        """Never raises.

        Extraction failing must not fail the turn - the player already has
        their narration, and losing one turn's memory is a far smaller problem
        than losing the turn. A miss gets picked up next time the thing comes
        up, because named things recur.
        """
        if not narration.strip():
            return Extraction()

        # Showing what already exists is the single biggest lever on duplicate
        # entries. Without it the model names the same guard "the guard", then
        # "cart guard", then "the gate guard" - three slugs, three rows, three
        # separate histories for one person.
        roster = ", ".join(sorted(known or [])[:80]) or "nothing yet"
        system, _ = render_prompt(
            "extract_entities", passage="(see message)", known=roster
        )
        try:
            result = await self._ai.complete_structured(
                CompletionRequest(
                    capability=Capability.EXTRACT_ENTITIES,
                    system=system,
                    messages=[Message(role="user", content=narration)],
                    response_model=Extraction,
                    max_tokens=1500,
                    session_id=session_id,
                )
            )
        except Exception:
            return Extraction()

        extraction = result.parsed if isinstance(result.parsed, Extraction) else Extraction()
        return Extraction(
            entities=extraction.entities[:MAX_ENTITIES_PER_TURN],
            facts=extraction.facts[:MAX_FACTS_PER_TURN],
        )
