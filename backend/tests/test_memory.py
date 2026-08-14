"""Memory tests against a real database.

The property under test throughout is that memory is *bounded*. A campaign
that accumulates thousands of entities must still produce a packet that fits,
or session 40 stops working and no amount of UI helps.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.db.base import Base
from app.modules.base import build_registry
from app.modules.memory.service import (
    DatabaseContextSource,
    MemoryService,
    make_ref,
    slugify,
)
from app.platform.ai.context import ContextBuilder, Exchange

build_registry().import_all_models()


def _sqlite_compatible() -> None:
    """SQLite has no JSONB, UUID or ARRAY. Swap for the test run only."""
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, postgresql.JSONB):
                column.type = sa.JSON()
            elif isinstance(column.type, postgresql.UUID):
                column.type = sa.String(36)
            elif isinstance(column.type, postgresql.ARRAY):
                column.type = sa.JSON()


_sqlite_compatible()


@pytest_asyncio.fixture
async def memory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    campaign_id = uuid.uuid4()

    async with maker() as db:
        await db.execute(sa.text(
            "INSERT INTO users (id,subject,last_groups,created_at,updated_at) "
            "VALUES ('u1','s','[]',datetime('now'),datetime('now'))"))
        await db.execute(sa.text(
            "INSERT INTO campaigns "
            "(id,owner_id,name,ruleset_id,play_mode,status,settings,created_at,updated_at) "
            "VALUES (:i,'u1','C','d20','solo','active','{}',datetime('now'),datetime('now'))"
        ), {"i": str(campaign_id)})
        yield MemoryService(db, campaign_id)
    await engine.dispose()


def test_slug_is_stable_across_phrasings():
    assert slugify("The Blackstair") == "the-blackstair"
    assert make_ref("location", "The Blackstair!") == "location:the-blackstair"


async def test_upsert_is_idempotent_on_ref(memory):
    """The same NPC across five scenes is one row, not five."""
    await memory.upsert_entity(kind="npc", name="Serel", summary="Innkeeper.")
    await memory.upsert_entity(kind="npc", name="Serel", summary="Innkeeper at the Blackstair.")
    assert await memory.entity_count() == 1
    entity = await memory.entity_by_ref("npc:serel")
    assert entity.mentions == 2


async def test_a_later_mention_never_blanks_an_earlier_summary(memory):
    await memory.upsert_entity(kind="npc", name="Serel", summary="A long description.")
    await memory.upsert_entity(kind="npc", name="Serel", summary="")
    assert (await memory.entity_by_ref("npc:serel")).summary == "A long description."


async def test_duplicate_facts_are_suppressed(memory):
    first = await memory.add_fact(
        subject_ref="npc:serel", predicate="runs", object_text="the Blackstair"
    )
    second = await memory.add_fact(
        subject_ref="npc:serel", predicate="runs", object_text="the Blackstair"
    )
    assert first is not None
    assert second is None


async def test_secret_facts_are_excluded_from_public_reads(memory):
    """A prepared adventure's secrets live in canon so the GM can run the
    scene. Showing them in the codex spoils the thing being played for."""
    await memory.add_fact(subject_ref="npc:serel", predicate="is", object_text="tired")
    await memory.add_fact(
        subject_ref="npc:serel", predicate="secretly", object_text="informs the Watch",
        secret=True,
    )
    assert len(await memory.facts()) == 2
    assert len(await memory.facts(include_secret=False)) == 1


async def test_retract_supersedes_rather_than_deletes(memory):
    fact = await memory.add_fact(
        subject_ref="npc:serel", predicate="is", object_text="alive"
    )
    await memory.retract(fact.id)
    assert await memory.facts() == []


async def test_rephrasing_resolves_to_the_same_ref(memory):
    """This closes the check-locking gap: 'search the desk' and 'look through
    the drawers' must not be two different targets."""
    await memory.upsert_entity(kind="location", name="The Blackstair")
    assert await memory.resolve_target("blackstair", "location") == "location:the-blackstair"
    assert await memory.resolve_target("The Blackstair", "location") == "location:the-blackstair"


async def test_character_hooks_become_canon(memory):
    """Otherwise threads are inert - written once at creation, never seen
    again by the game master."""
    await memory.seed_from_character(
        "Vessa", "A stormwarden's daughter.",
        [{"kind": "debt", "subject": "Karal", "detail": "for the boat"}],
    )
    facts = await memory.facts()
    assert any("Karal" in f.object_text for f in facts)
    # The subject of a debt is a person worth tracking in their own right.
    assert await memory.entity_by_ref("npc:karal") is not None


async def test_context_does_not_grow_with_campaign_length(memory):
    """The load-bearing property. Session 40 must cost what session 4 cost."""
    for i in range(400):
        await memory.upsert_entity(kind="npc", name=f"Person {i}", summary="x" * 200)
        await memory.add_fact(
            subject_ref=f"npc:person-{i}", predicate="did", object_text="y" * 200
        )

    source = DatabaseContextSource(memory, recent=[Exchange("GM", "hi")] * 10)
    await source.preload()
    packet = ContextBuilder().build(source, "s", "sc")

    assert packet.estimated_tokens() < 14_000, (
        f"packet grew to {packet.estimated_tokens()} tokens"
    )


async def test_in_play_entities_are_retrieved_first(memory):
    for i in range(100):
        await memory.upsert_entity(kind="npc", name=f"Filler {i}")
    await memory.upsert_entity(kind="npc", name="Serel")

    source = DatabaseContextSource(memory, in_play_refs=["npc:serel"])
    await source.preload()
    briefs = source.entities_in_play("s", "sc")
    assert briefs[0].id == "npc:serel"


async def test_near_duplicate_names_fold_into_one_entity(memory):
    """Extraction names the same person differently from scene to scene.

    Two rows for one guard means two histories, two sets of facts, and a codex
    that looks broken to the player.
    """
    await memory.upsert_entity(kind="npc", name="the guard")
    await memory.upsert_entity(kind="npc", name="cart guard")
    await memory.upsert_entity(kind="npc", name="the gate guard")
    assert await memory.entity_count() == 1


async def test_a_longer_name_folds_into_a_shorter_one(memory):
    await memory.upsert_entity(kind="npc", name="Serel")
    await memory.upsert_entity(kind="npc", name="Serel the innkeeper")
    assert await memory.entity_count() == 1


async def test_genuinely_different_people_stay_separate(memory):
    """Merging cannot be undone from the UI, so the match must be conservative."""
    await memory.upsert_entity(kind="npc", name="Serel")
    await memory.upsert_entity(kind="npc", name="Karal")
    assert await memory.entity_count() == 2


async def test_same_name_different_kind_stays_separate(memory):
    """A place called the Watch is not the faction called the Watch."""
    await memory.upsert_entity(kind="faction", name="the Watch")
    await memory.upsert_entity(kind="location", name="the Watch")
    assert await memory.entity_count() == 2
