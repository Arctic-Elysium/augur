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

# Two, not four. Four per turn is 170 entries across a 43-turn session, which
# is a codex nobody reads and a review queue nobody drains. A turn that truly
# introduces three durable things is rare; a turn that introduces two is
# already unusual. Misses get caught the next time the thing is mentioned.
MAX_ENTITIES_PER_TURN = 2
MAX_FACTS_PER_TURN = 3

# Words that mark a description rather than a name. A "the" in front is the
# single clearest signal that the model has extracted a role, not a person.
_ARTICLES = ("the ", "a ", "an ", "some ", "another ")

# Kinds where an unnamed entry is almost always scenery.
#
# This used to be {"npc", "faction"} while the prompt was strict about every
# kind, so locations, items and creatures bypassed the check entirely and
# "the alley", "a lamp" and "the grey horse" walked straight into the codex.
# The prompt alone never holds this line: roles and props recur, so the model
# has every reason to keep offering them.
_NAME_REQUIRED = {"npc", "faction", "location", "item", "creature"}

# `concept` has no naming convention to check, so it is filtered by kind
# instead: it exists for things like "the Ashfell Accord", and in practice it
# is where the model files anything it could not classify. Unrecognised kinds
# land here too, via the EntityKind fallback in the service.
_ALLOWED_KINDS = {"npc", "location", "faction", "item", "creature", "concept"}


def _is_named(name: str) -> bool:
    """Does this read as a proper name rather than a description?

    A prompt alone does not hold this line - the model will keep offering "the
    barmaid" because she genuinely recurs. The check is mechanical: a real name
    has a capitalised word that is not just the sentence-initial article.
    """
    cleaned = name.strip()
    if not cleaned:
        return False
    lowered = cleaned.lower()
    if any(lowered.startswith(a) for a in _ARTICLES):
        return False
    # At least one capitalised word that is not a leading article.
    return any(w[:1].isupper() for w in cleaned.split())


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
        # Ranked, not alphabetical. `sorted(...)[:80]` looks harmless and is
        # a dedupe bug that gets worse as the campaign gets longer: past 80
        # entities everything late in the alphabet silently stops being shown,
        # so the extractor re-coins those names as new entries and Vareth
        # splits into a second Vareth. Callers pass most-mentioned first.
        roster = ", ".join((known or [])[:80]) or "nothing yet"
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

        extraction = (
            result.parsed if isinstance(result.parsed, Extraction) else Extraction()
        )

        # Enforce the naming rule in code, not just in the prompt. Roles recur
        # constantly - "the barmaid" appeared thirteen times in one session -
        # so the model has every reason to keep offering them.
        entities = [
            e
            for e in extraction.entities
            if e.kind in _ALLOWED_KINDS
            and (e.kind not in _NAME_REQUIRED or _is_named(e.name))
        ][:MAX_ENTITIES_PER_TURN]

        # Facts whose subject was filtered out have nothing to attach to.
        kept = {e.name.lower() for e in entities}
        facts = [f for f in extraction.facts if f.subject.lower() in kept][
            :MAX_FACTS_PER_TURN
        ]
        return Extraction(entities=entities, facts=facts)
