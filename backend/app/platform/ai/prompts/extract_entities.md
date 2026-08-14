<!-- version: 3.0.0 -->
Extract durable entities and facts from the passage below.

An **entity** is a person, place, faction, item, or creature that was *named*,
or that the player will plausibly refer to again. Give it a `kind` of one of:
npc, location, faction, item, creature, concept.

A **fact** is something the world must not later contradict: a name, a
relationship, a property, something that happened. Break each into subject,
predicate, object. The subject should be an entity name you also extracted.

# Already on record

{known}

If the passage refers to one of these, **use that exact name**. Do not coin a
new one for someone already known - "the guard", "cart guard" and "the gate
guard" are one person, and inventing a name per mention splits their history
across three entries.

Only introduce a new entity when it is genuinely new.

Be conservative. A wrong entity is worse than a missing one - it pollutes
retrieval and shows up in the player's codex looking like a mistake. Anything
genuinely important will be mentioned again, and caught then.

Do not extract:
- atmosphere, weather, lighting, or mood
- adjectives and descriptions with no named subject
- anything the passage merely implied rather than stated
- the player characters themselves; they are already tracked

Return JSON matching this shape exactly. No preamble, no code fences.

{{
  "entities": [{{"kind": "npc", "name": "Serel", "summary": "The innkeeper at the Blackstair."}}],
  "facts": [{{"subject": "Serel", "predicate": "works at", "object": "the Blackstair inn"}}]
}}

If nothing durable was established, return empty arrays.

Passage:
{passage}
