<!-- version: 4.0.0 -->
Extract only entities that will still matter in three sessions.

# Already on record

{known}

If the passage refers to one of these, **use that exact name**. Do not coin a
new one for someone already known - "the guard", "cart guard" and "the gate
guard" are one person, and a name per mention splits their history apart.

# What counts

**npc** — only people with a *proper name*. "Aldric Vorn" yes. "the barmaid",
"the guard", "the younger man", "a merchant" — no. An unnamed person is
scenery until they are named; if they matter, the passage will name them.

**location** — only places the party could deliberately return to and ask for
by name. "The Blackstair", "the east gate" yes. "the alley", "the square",
"the washstand", "the common room" — no. A room is not a location. Furniture
is certainly not.

**item** — only objects that are singular, plot-bearing, or belong to someone.
"the sealed vial from Vorn's cart" yes. "bottle", "vial", "lamp", "cart",
"box" — no. If the passage would read the same with the object replaced by
another of its kind, it is a prop, not an entity.

**faction** — only named organisations.

**creature** — only named or singular creatures. "the grey horse" is a horse.

# Facts

Attach facts only to entities you extracted. Subject must be an entity name.

# Be ruthless

Most passages contain **nothing** worth recording. Returning empty arrays is
the common, correct answer. A wrong entity is far worse than a missing one: it
pollutes retrieval, wastes budget, and shows up in the player's codex looking
like a mistake. Anything that genuinely matters gets mentioned again, and is
caught then.

Never extract: the player characters, atmosphere, weather, mood, body parts,
clothing, food, furniture, generic props, or anything the passage merely
implied.

Return JSON matching this shape exactly. No preamble, no code fences.

{{
  "entities": [{{"kind": "npc", "name": "Aldric Vorn", "summary": "A carter moving sealed vials through the east gate."}}],
  "facts": [{{"subject": "Aldric Vorn", "predicate": "moves goods through", "object": "the east gate"}}]
}}

Passage:
{passage}
