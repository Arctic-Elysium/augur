# Augur

An AI game master engine. Three play modes over one core:

| Mode | Players | GM | Turn arbitration |
|---|---|---|---|
| `solo` | 1 | AI | Immediate |
| `party` | many | AI | Batched rounds |
| `table` | many | Human | Human-gated, AI proposes only |

## Architecture

```
backend/app/
  core/          # cross-cutting, no game logic
    auth/        # Voidauth OIDC + signed cookie sessions (ported from Tome/Cairn)
    config/      # settings
    db/          # declarative base, async engine
    errors/      # error taxonomy -> HTTP status mapping
  platform/      # infrastructure the game sits on
    ai/          # gateway: capabilities, routing, backends  <- the Golem seam
    events/      # (Milestone 5) pub/sub for realtime
    observability/
  modules/       # feature areas, each self-contained
    identity/    # user rows keyed on OIDC sub
    campaigns/   # campaign + membership
    sessions/    # (M3) play sessions, turn loop
    characters/  # (M1) sheets
    rules/       # (M1) pure functions: dice, checks, combat. No I/O.
    world/       # (M5) generation ladder + map
    memory/      # (M4) entity store, event log, canon
    narrative/   # (M3) GM voice
```

### The module contract

Each feature area exposes a `module.py` with a class implementing `Module`:
a `name`, a `router()`, and `import_models()`. Wire it into `build_registry()`
in `app/modules/base.py` and it mounts at `/api/<name>` and becomes visible to
Alembic autogenerate. Nothing outside a module imports its internals.

Adding a feature is adding a directory and one line.

### The AI gateway

Nothing imports an AI SDK directly. Game code asks for a **capability**
(`narrate_scene`, `generate_region`, `resolve_turn`…); `config/ai_routing.yaml`
maps capabilities to backends with weights and fallbacks.

Each capability declares requirements (structured output, streaming, tool use,
minimum context). Each backend declares what it offers. **The router validates
this at startup**, so a backend that can't satisfy a capability fails the
readiness probe rather than a player's turn.

Swapping Golem in later: add the backend, shift weight on one capability at a
time, compare, ramp. No game code changes.

### Rules are not the model's job

The model may *call* the rules engine (tool use) but never *decides* outcomes.
Dice, checks, damage, and conditions live in `modules/rules` as pure functions.
Illegal actions raise `RuleViolation` (422), never a 500.

## Local development

```bash
cp .env.example .env          # set SESSION_SECRET: openssl rand -hex 32
make up                       # postgres
make migrate
make dev                      # api on :8000
make web                      # vite on :5173, proxies /api
```

## Security posture

Carried over from the Tome review — do not relax:

- Identity is keyed on the OIDC `sub` claim. **Never** on `email`.
- Authorization reads groups from a signature-verified ID token only.
- `SESSION_SECRET` under 32 chars is rejected outside `local`.
- `/docs` and `/openapi.json` are local-only.
- Container runs non-root, read-only root filesystem, all capabilities dropped.
- Secrets come from an existing k8s Secret, never from `values.yaml`.

## Milestone 0 — done

- [x] Repo scaffold, module contract, registry
- [x] Settings with environment-aware secret validation
- [x] Voidauth OIDC: auth code + PKCE, JWKS with rotation, nonce/state checks
- [x] Signed HttpOnly cookie sessions
- [x] Postgres schema v1 + Alembic (users, campaigns, campaign_members)
- [x] AI gateway: capabilities, requirement validation, weighted routing,
      fallback, per-session token ledger, Anthropic backend
- [x] Prometheus metrics
- [x] Helm chart: Deployment, Service, HTTPRoute, ServiceMonitor
- [x] Frontend shell with auth-gated routing

Deliberately deferred: structured logging, CI pipeline, migration hook in the
chart. All are production concerns, none block building the game.

## Migrations

Your ORM models say what the tables *should* look like; Postgres has whatever
you last gave it. Alembic generates and versions the diff between the two.

Right now the chart has no migration hook — run them yourself:

```bash
make migrate                          # apply everything pending
make revision m="add sessions table"  # generate a new one from model changes
```

While the schema is churning and there's no data worth keeping, dropping and
recreating the database is often faster than writing a migration. Once campaign
state matters (~Milestone 4), add a `pre-upgrade` Job to the chart so deploys
migrate before new pods start.

## Milestone 1 — done

Rules engine. Pure functions, no I/O, 67 tests.

```
modules/rules/
  types.py       # dice, tiers, results, boons, conditions, clocks, deltas
  dice.py        # notation parser, injectable RNG, advantage/disadvantage
  protocol.py    # the Ruleset seam - swap systems without touching game code
  locking.py     # check ledger: stops retry-farming a natural 20
  engine.py      # facade the turn loop and AI tools call
  registry.py    # add a system = a directory + one line
  systems/d20/   # the primary ruleset
```

### The primary system

d20 roll-over, six attributes, five degrees of success:

| Margin | Tier |
|---|---|
| +10 or more | Critical success |
| 0 to +9 | Success |
| −1 to −4 | Partial — you get it, but it costs |
| −5 to −9 | Failure |
| −10 or worse | Critical failure |

**Natural 20 and 1 are absolute** — read off the raw die face, before
modifiers, and they override the arithmetic entirely. Advantage and
disadvantage read the *kept* die, so advantage doesn't secretly double the crit
rate.

**Every critical owes a boon or a setback.** The engine picks the category
(extra resource, extra information, position gained, clock reduced…) and the
scale (minor/standard/major, derived from the DC). The model invents the
specific thing within those bounds. That guarantees the "and something extra"
always shows up and caps how large it can get — a crit on a trivial search
can't produce a relic.

### Two guards worth understanding

**The roll gate.** Absolute criticals mean a nat 20 always crits, so a task
more than 20 above the character's modifier is an auto-failure that never
reaches the dice. Without this, patience defeats any difficulty. The reverse
holds too: trivial tasks auto-pass so a nat 1 can't make a competent character
look foolish at nothing.

**Check locking.** A resolved check against a static target is recorded; a
re-attempt returns the stored result instead of rolling again. Locks reopen
when something material changes — a different actor, a new condition, a new
relevant item, a new scene, or a *push* where the actor spends a resource to
force the retry. Attacks and other checks against active opponents never lock.

Lock keys use a stable `target_ref`, not phrasing, so "search the desk" and
"look through the drawers" can't launder a retry. Resolving those to the same
entity ID is the memory module's job in Milestone 4.

## Milestone 2 — done

AI gateway. 122 tests, all runnable without network or spend.

```
platform/ai/
  gateway.py     # capabilities, requirements, request/result types
  router.py      # weighted routing, fallback, retry, metrics, budget
  tools.py       # the tool surface over the rules engine
  executor.py    # validation: the layer between a confident model and the DB
  context.py     # bounded context assembly
  prompts/       # versioned templates, in files not strings
  backends/      # anthropic, openai-compatible, fake
```

### The governing rule

> The model chooses **which** tool to call. The engine decides **what happens**.

So there is a `roll_check` tool that takes a check kind and a difficulty
*band*, and no tool anywhere that accepts a raw DC or an outcome. Tests assert
this by walking every tool schema — if someone later adds a `dc` field, the
suite fails.

Difficulty is a coarse enum (trivial … extreme) that maps to a fixed number.
Situational factors come from a closed list, cap at two, and swing at most ±4 —
stacking circumstances is how a model talks itself into an easy win. And
`roll_check` requires a one-line `reason`, which makes the model state its case
and gives you a log of *why* each check happened.

### Validation

Every call is scoped to the current turn: the model may act on actors in this
scene and no others, so a hallucinated actor id can't quietly mutate a
character who isn't in the room. Magnitudes are bounded — 30 damage, 12 clock
segments, 3 segments advanced per call — because a model that decides the fall
"obviously" kills you shouldn't be able to one-shot a character.

Rejections come back as **tool results, not exceptions**, so the model reads
the error and corrects itself inside the same turn. Each one increments
`augur_rule_violations_total{source="model"}`. A healthy session produces
almost none; a spike means a prompt has drifted.

### Context assembly

The load-bearing property: **context size is bounded and does not grow with
campaign length.** Session 40 costs roughly what session 4 cost. Five layers,
each independently capped:

| Layer | Holds |
|---|---|
| `canon` | Durable facts the world may not contradict |
| `entities` | Who and what is in play right now |
| `history` | Summary ladder: chapter → session → scene |
| `previous` | The prior session at near-full fidelity |
| `recent` | The last few exchanges, verbatim |

`previous` is deliberately separate from the ladder. Summaries lose exactly
what continuity needs — who said what, what was left half-done — so the session
immediately behind the player stays close to verbatim while everything older
compresses. It keeps its *tail*, since how a session ended is what you need to
pick the thread back up.

Assembly is deterministic and code-driven. The model never chooses what to
remember; it receives a packet this module built. "Read the campaign and recall
what matters" fails at exactly the length where it would start to matter.

Milestone 2 ships the interface and a naive in-memory source. Milestone 4
replaces it with the real ladder and entity store behind the same interface.

## Milestone 3 — done

Solo play loop. 137 tests.

### One player, several characters

Solo mode runs a party, not a single sheet. A turn either names an actor or
addresses the party as a whole, and both go through the same loop — the only
difference is who the model is told is acting. That matters more than it looks:
with four characters an unqualified "you" is ambiguous, and a model resolving
that on its own picks differently turn to turn.

Check locks are per actor, so each character gets their own attempt at the same
desk. Vessa failing doesn't stop Ordo trying — they're different people with
different hands and eyes.

Characters carry a `controller` field (`player` / `ai`). Everything defaults to
`player`; the field exists now so AI-run companions later don't need a
migration.

This is also why party mode is nearly free: solo-with-a-party and
multiplayer-with-one-human are the same problem. Milestone 5's networking
becomes a transport concern rather than a rewrite of game logic.

### The loop

```
player input
  -> model with tools        (bounded: 6 rounds, then fail loudly)
  -> executor validates      (rejections return as tool results)
  -> engine resolves         (dice, deltas, locks)
  -> model narrates
  -> persist                 (turn, party, clocks, locks)
```

State is applied to the working scope immediately, so a model that damages a
character then queries them gets the truth rather than stale HP.

### Streaming

Turns stream over SSE. Mechanics are emitted as they resolve, then narration —
a player watching dice land experiences four seconds very differently from a
player watching a spinner. SSE rather than websockets because a solo turn is
one-directional; Milestone 5 adds websockets when multiplayer needs a
bidirectional channel.

Dice render distinctly from prose in the UI. Mechanics folded into narration
are easy to miss, and missing a boon or a clock tick means missing the part the
engine guaranteed.

### Durability

Everything survives a restart. Sessions carry a `seed`, so every roll can be
replayed exactly when someone disputes an outcome weeks later. Check locks
persist to Postgres — an in-memory ledger would reset on pod restart and
quietly hand the player a fresh attempt at everything they already failed,
which is the exact exploit locking exists to close. Clocks are campaign-scoped,
because a faction's plan doesn't reset because you stopped playing for the
night.

Turns store `tool_calls` and `deltas` alongside the prose, so a turn can be
audited — you can see which checks fired and what changed, rather than
inferring it from narration that may have drifted.

## Next — Milestone 4: memory and canon
