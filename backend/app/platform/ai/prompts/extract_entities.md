<!-- version: 1.0.0 -->
Extract durable facts and entities from the passage below.

A **fact** is something the world must not later contradict: a name, a
relationship, a location, an event that happened, a property of a thing. Break
each into subject, predicate, object. Prefer specific over general.

An **entity** is a person, place, item, or faction that was named or that the
player may plausibly refer to again. Give each a stable id in the form
`kind:slug` - `npc:the-duke`, `loc:study`, `item:brass-key`, `faction:watch`.

Do not extract atmosphere, adjectives, or anything the narration merely
implied. If it was not stated, it is not a fact.

Respond with JSON only. No preamble, no code fences.

Passage:
{passage}
