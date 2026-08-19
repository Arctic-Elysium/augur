"""Context assembly.

The single most important property of this module: **context size is bounded
and does not grow with campaign length.** Session 40 must cost roughly what
session 4 cost, or long campaigns are impossible on both budget and coherence
grounds.

That is why assembly is deterministic and code-driven. The model never chooses
what to remember - it receives a packet this module built. "Read the campaign
and recall what matters" fails at exactly the length where it would start to
matter, because by then the campaign no longer fits.

A packet has four layers, each independently capped:

    canon        durable facts the world must not contradict
    entities     who and what is in play right now
    history      a summary ladder: chapter -> session -> scene
    previous     the prior session at near-full fidelity
    recent       the last few exchanges, verbatim

The `previous` layer is deliberately separate from the ladder. Summaries lose
exactly what continuity needs - who said what, what was left half-done - so the
session immediately behind the player stays close to verbatim while everything
older compresses. This is what makes a resume feel seamless rather than like
being briefed on your own campaign.

Milestone 2 ships the interface and a naive builder that reads whatever it is
handed. Milestone 4 replaces the builder with the real ladder and entity store
behind this same interface - nothing upstream changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

# Rough budget split. Tuned so a packet lands near 8k tokens regardless of
# whether the campaign is three sessions old or three hundred.
DEFAULT_BUDGET = {
    "primer": 2_000,
    "canon": 1_500,
    "entities": 1_500,
    "history": 2_000,
    "previous": 4_000,
    "recent": 3_000,
}

CHARS_PER_TOKEN = 4  # crude but stable enough for budgeting


@dataclass(frozen=True)
class CanonFact:
    """Something established that the world may not later contradict.

    Extracted from model output rather than trusted to memory. `subject` and
    `predicate` exist so contradictions can be detected mechanically: two facts
    with the same subject and predicate and different objects is a conflict,
    not a nuance.
    """

    subject: str
    predicate: str
    object: str
    established_at: str = ""

    def render(self) -> str:
        return f"{self.subject} {self.predicate} {self.object}"


@dataclass(frozen=True)
class EntityBrief:
    """An NPC, item, faction, or location currently in play."""

    id: str
    kind: str
    name: str
    summary: str
    disposition: str | None = None

    def render(self) -> str:
        head = f"[{self.id}] {self.name} ({self.kind})"
        if self.disposition:
            head += f" - {self.disposition}"
        return f"{head}: {self.summary}"


@dataclass(frozen=True)
class Summary:
    """One rung on the ladder. `level` 0 is a scene, 1 a session, 2 a chapter."""

    level: int
    label: str
    text: str


@dataclass(frozen=True)
class Exchange:
    """One verbatim turn, player input and GM response."""

    speaker: str
    text: str


@dataclass
class ContextPacket:
    # Player-supplied setting notes, verbatim and capped. This is how a
    # prepared campaign - a published starter set summarised in the owner's
    # own words - enters play without falling out of the window: it is pinned
    # here every turn rather than trusted to a giant first prompt.
    primer: str = ""
    canon: list[CanonFact] = field(default_factory=list)
    entities: list[EntityBrief] = field(default_factory=list)
    history: list[Summary] = field(default_factory=list)
    previous: list[Exchange] = field(default_factory=list)
    recent: list[Exchange] = field(default_factory=list)
    truncated: dict[str, int] = field(default_factory=dict)

    def render(self) -> str:
        """The packet as it appears in the system prompt."""
        blocks: list[str] = []

        if self.primer:
            blocks.append(
                "## The setting, as given\n"
                "The owner of this campaign wrote this. It outranks anything "
                "you would otherwise invent; where it is silent, invent "
                "freely and consistently with it.\n" + self.primer
            )

        if self.canon:
            facts = "\n".join(f"- {f.render()}" for f in self.canon)
            blocks.append(
                "## Established facts\n"
                "These are settled. Do not contradict them; if the player "
                "assumes otherwise, correct them in the fiction.\n" + facts
            )

        if self.entities:
            briefs = "\n".join(f"- {e.render()}" for e in self.entities)
            blocks.append(
                "## In play\n"
                "Refer to these by their bracketed id when calling tools.\n" + briefs
            )

        if self.history:
            for summary in sorted(self.history, key=lambda s: -s.level):
                heading = {2: "Chapter", 1: "Session", 0: "Scene"}.get(
                    summary.level, "Earlier"
                )
                blocks.append(f"## {heading}: {summary.label}\n{summary.text}")

        if self.previous:
            lines = "\n".join(f"{e.speaker}: {e.text}" for e in self.previous)
            blocks.append(
                "## Last session\n"
                "The session immediately before this one, in the party's own "
                "words. Pick the thread up from here.\n" + lines
            )

        if self.recent:
            lines = "\n".join(f"{e.speaker}: {e.text}" for e in self.recent)
            blocks.append("## Immediately before now\n" + lines)

        return "\n\n".join(blocks)

    def estimated_tokens(self) -> int:
        return len(self.render()) // CHARS_PER_TOKEN


class ContextSource(Protocol):
    """What the memory module must provide. Implemented naively in Milestone 2,
    properly in Milestone 4 - the interface does not change."""

    def canon_for_scene(self, session_id: str, scene_id: str) -> list[CanonFact]: ...
    def entities_in_play(self, session_id: str, scene_id: str) -> list[EntityBrief]: ...
    def summary_ladder(self, session_id: str) -> list[Summary]: ...
    def previous_session(self, session_id: str) -> list[Exchange]: ...
    def recent_exchanges(self, session_id: str, limit: int) -> list[Exchange]: ...


class ContextBuilder:
    """Assembles a bounded packet. Drops from the *oldest* end of each layer
    when over budget, because recency is the best proxy for relevance and the
    summary ladder already carries what was dropped."""

    def __init__(self, budget: dict[str, int] | None = None) -> None:
        self._budget = {**DEFAULT_BUDGET, **(budget or {})}

    def build(
        self, source: ContextSource, session_id: str, scene_id: str,
        *, recent_limit: int = 12, primer: str = "",
    ) -> ContextPacket:
        packet = ContextPacket()
        truncated: dict[str, int] = {}

        cap = self._budget["primer"] * CHARS_PER_TOKEN
        packet.primer = primer.strip()[:cap]

        packet.canon, truncated["canon"] = self._fit(
            source.canon_for_scene(session_id, scene_id),
            self._budget["canon"], lambda f: f.render(),
        )
        packet.entities, truncated["entities"] = self._fit(
            source.entities_in_play(session_id, scene_id),
            self._budget["entities"], lambda e: e.render(),
        )
        # History fits highest-level summaries first: a chapter digest is worth
        # more per token than a scene note when space is tight.
        ladder = sorted(source.summary_ladder(session_id), key=lambda s: -s.level)
        packet.history, truncated["history"] = self._fit(
            ladder, self._budget["history"], lambda s: s.text,
        )
        # The prior session keeps its *tail*: how a session ended matters more
        # for picking the thread back up than how it opened.
        prior = source.previous_session(session_id)
        kept_prev, dropped_prev = self._fit(
            list(reversed(prior)), self._budget["previous"], lambda e: e.text,
        )
        packet.previous = list(reversed(kept_prev))
        truncated["previous"] = dropped_prev

        exchanges = source.recent_exchanges(session_id, recent_limit)
        kept_recent, dropped_recent = self._fit(
            list(reversed(exchanges)), self._budget["recent"], lambda e: e.text,
        )
        packet.recent = list(reversed(kept_recent))
        truncated["recent"] = dropped_recent

        packet.truncated = {k: v for k, v in truncated.items() if v}
        return packet

    @staticmethod
    def _fit(items, budget_tokens, render):
        """Keep items in order until the budget runs out. Returns (kept, dropped)."""
        kept, used = [], 0
        for item in items:
            cost = len(render(item)) // CHARS_PER_TOKEN + 1
            if used + cost > budget_tokens:
                return kept, len(items) - len(kept)
            kept.append(item)
            used += cost
        return kept, 0


class InMemoryContextSource:
    """Milestone 2 stand-in. Holds what it is given; no ladder, no retrieval.

    Exists so the turn loop can be built and tested now. Milestone 4 swaps in
    the real implementation without touching a line upstream.
    """

    def __init__(self) -> None:
        self.canon: list[CanonFact] = []
        self.entities: list[EntityBrief] = []
        self.summaries: list[Summary] = []
        self.prior: list[Exchange] = []
        self.exchanges: list[Exchange] = []

    def canon_for_scene(self, session_id: str, scene_id: str) -> list[CanonFact]:
        return list(self.canon)

    def entities_in_play(self, session_id: str, scene_id: str) -> list[EntityBrief]:
        return list(self.entities)

    def summary_ladder(self, session_id: str) -> list[Summary]:
        return list(self.summaries)

    def previous_session(self, session_id: str) -> list[Exchange]:
        return list(self.prior)

    def recent_exchanges(self, session_id: str, limit: int) -> list[Exchange]:
        return self.exchanges[-limit:]
