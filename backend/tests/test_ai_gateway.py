"""AI gateway tests.

Everything here runs against the fake backend: no network, no API key, no
tokens spent. That is the point of having one - a gateway you can only test by
paying for it is a gateway that stops being tested.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.core.errors import BudgetExceeded, UpstreamError
from app.platform.ai.backends.fake import FakeBackend
from app.platform.ai.context import (
    CanonFact,
    ContextBuilder,
    EntityBrief,
    Exchange,
    InMemoryContextSource,
    Summary,
)
from app.platform.ai.gateway import (
    BackendCapabilities,
    Capability,
    CompletionRequest,
    CompletionResult,
    Message,
    Usage,
)
from app.platform.ai.router import AIRouter, Route, TokenLedger


def request(capability=Capability.NARRATE_SCENE, **overrides) -> CompletionRequest:
    base = {
        "capability": capability,
        "system": "You are a game master.",
        "messages": [Message(role="user", content="I open the door.")],
    }
    base.update(overrides)
    return CompletionRequest(**base)


def router_with(backends, routes, budget=1_000_000) -> AIRouter:
    return AIRouter(backends, routes, TokenLedger(budget))


# ------------------------------------------------------------ startup validation


def test_under_capable_backend_is_rejected_at_startup():
    """A misconfiguration must fail the readiness probe, not a player's turn."""
    weak = FakeBackend(
        "weak",
        capabilities=BackendCapabilities(
            structured_output=False, streaming=True, tool_use=True,
            context_tokens=200_000,
        ),
    )
    with pytest.raises(ValueError, match="structured output"):
        router_with({"weak": weak}, {Capability.GENERATE_WORLD: [Route("weak", "m")]})


def test_insufficient_context_window_is_rejected():
    small = FakeBackend(
        "small",
        capabilities=BackendCapabilities(True, True, True, context_tokens=8_000),
    )
    with pytest.raises(ValueError, match="context"):
        router_with({"small": small}, {Capability.SUMMARIZE: [Route("small", "m")]})


def test_route_to_unknown_backend_is_rejected():
    with pytest.raises(ValueError, match="unknown backend"):
        router_with({}, {Capability.NARRATE_SCENE: [Route("ghost", "m")]})


def test_capable_backend_passes_validation():
    router = router_with(
        {"fake": FakeBackend("fake")},
        {c: [Route("fake", "m")] for c in Capability},
    )
    assert router is not None


# ------------------------------------------------------------ routing


async def test_completion_returns_backend_text():
    backend = FakeBackend("fake", responses=["The door swings open."])
    router = router_with({"fake": backend}, {Capability.NARRATE_SCENE: [Route("fake", "m")]})
    result = await router.complete(request())
    assert "swings open" in result.text
    assert result.backend == "fake"


async def test_weighted_routing_splits_traffic():
    """Weighted routes are how a Golem rollout ramps: 10%, then 50%, then all."""
    primary = FakeBackend("primary", responses=["primary"] * 500)
    candidate = FakeBackend("candidate", responses=["candidate"] * 500)
    router = router_with(
        {"primary": primary, "candidate": candidate},
        {Capability.NARRATE_SCENE: [
            Route("primary", "m", weight=80),
            Route("candidate", "m", weight=20),
        ]},
    )
    for _ in range(400):
        await router.complete(request())

    total = primary.call_count + candidate.call_count
    assert total == 400
    share = candidate.call_count / total
    assert 0.10 < share < 0.32, f"candidate took {share:.0%}, expected ~20%"


async def test_fallback_engages_when_primary_fails():
    broken = FakeBackend("broken", fail=True)
    backup = FakeBackend("backup", responses=["rescued"])
    router = router_with(
        {"broken": broken, "backup": backup},
        {Capability.NARRATE_SCENE: [Route("broken", "m", fallback="backup")]},
    )
    result = await router.complete(request())
    assert result.text == "rescued"
    assert result.backend == "backup"


async def test_failure_without_fallback_propagates():
    router = router_with(
        {"broken": FakeBackend("broken", fail=True)},
        {Capability.NARRATE_SCENE: [Route("broken", "m")]},
    )
    with pytest.raises(Exception):
        await router.complete(request())


async def test_unrouted_capability_raises():
    router = router_with(
        {"fake": FakeBackend("fake")},
        {Capability.NARRATE_SCENE: [Route("fake", "m")]},
    )
    with pytest.raises(UpstreamError, match="no backend routed"):
        await router.complete(request(Capability.SUMMARIZE))


# ------------------------------------------------------------ budget


async def test_session_budget_is_enforced():
    backend = FakeBackend("fake", responses=["x"] * 50, usage=Usage(500, 500))
    router = router_with(
        {"fake": backend},
        {Capability.NARRATE_SCENE: [Route("fake", "m")]},
        budget=2_000,
    )
    for _ in range(2):
        await router.complete(request(session_id="s1"))
    with pytest.raises(BudgetExceeded):
        await router.complete(request(session_id="s1"))


async def test_budget_is_per_session():
    backend = FakeBackend("fake", responses=["x"] * 50, usage=Usage(500, 500))
    router = router_with(
        {"fake": backend},
        {Capability.NARRATE_SCENE: [Route("fake", "m")]},
        budget=2_000,
    )
    for _ in range(2):
        await router.complete(request(session_id="s1"))
    # A different session is unaffected by the first one's spend.
    await router.complete(request(session_id="s2"))


async def test_requests_without_session_id_are_not_budgeted():
    backend = FakeBackend("fake", responses=["x"] * 20, usage=Usage(5_000, 5_000))
    router = router_with(
        {"fake": backend},
        {Capability.NARRATE_SCENE: [Route("fake", "m")]},
        budget=100,
    )
    await router.complete(request())


# ------------------------------------------------------------ structured output


class WorldSpec(BaseModel):
    name: str
    tone: str


async def test_structured_output_parses():
    backend = FakeBackend(
        "fake", responses=['{"name": "Ashfell", "tone": "grim"}']
    )
    router = router_with(
        {"fake": backend}, {Capability.GENERATE_WORLD: [Route("fake", "m")]}
    )
    result = await router.complete_structured(
        request(Capability.GENERATE_WORLD, response_model=WorldSpec)
    )
    assert result.parsed.name == "Ashfell"


async def test_structured_output_retries_on_invalid_then_succeeds():
    """The usual failure is a prose preamble the model drops when told."""
    backend = FakeBackend(
        "fake",
        responses=["Here you go!", '{"name": "Ashfell", "tone": "grim"}'],
    )
    router = router_with(
        {"fake": backend}, {Capability.GENERATE_WORLD: [Route("fake", "m")]}
    )
    result = await router.complete_structured(
        request(Capability.GENERATE_WORLD, response_model=WorldSpec)
    )
    assert result.parsed.tone == "grim"
    assert backend.call_count == 2


async def test_structured_output_gives_up_after_bounded_attempts():
    """Unbounded retry on a confused model burns budget silently."""
    backend = FakeBackend("fake", responses=["nope"] * 10)
    router = router_with(
        {"fake": backend}, {Capability.GENERATE_WORLD: [Route("fake", "m")]}
    )
    with pytest.raises(UpstreamError, match="after 2 attempts"):
        await router.complete_structured(
            request(Capability.GENERATE_WORLD, response_model=WorldSpec)
        )
    assert backend.call_count == 2


# ------------------------------------------------------------ streaming


async def test_stream_yields_chunks():
    backend = FakeBackend("fake", responses=["The door swings wide."])
    router = router_with(
        {"fake": backend}, {Capability.NARRATE_SCENE: [Route("fake", "m")]}
    )
    chunks = [c async for c in router.stream(request())]
    assert "".join(chunks).strip() == "The door swings wide."
    assert len(chunks) > 1, "narration must stream, not arrive in one block"


# ------------------------------------------------------------ health


async def test_health_reports_each_backend():
    router = router_with(
        {"good": FakeBackend("good"), "bad": FakeBackend("bad", fail=True)},
        {Capability.NARRATE_SCENE: [Route("good", "m")]},
    )
    health = await router.health()
    assert health["good"] is True
    assert health["bad"] is False


# ------------------------------------------------------------ context assembly


def _source_with(n_exchanges=0, n_prior=0, n_canon=0, n_entities=0):
    source = InMemoryContextSource()
    source.canon = [
        CanonFact(f"npc-{i}", "is", "a person of note") for i in range(n_canon)
    ]
    source.entities = [
        EntityBrief(f"npc-{i}", "npc", f"Person {i}", "A person.") for i in range(n_entities)
    ]
    source.prior = [Exchange("GM", f"prior line {i}") for i in range(n_prior)]
    source.exchanges = [Exchange("GM", f"line {i}") for i in range(n_exchanges)]
    return source


def test_packet_renders_all_layers():
    source = _source_with(n_exchanges=2, n_prior=2, n_canon=1, n_entities=1)
    source.summaries = [Summary(2, "Chapter One", "They fled the city.")]
    packet = ContextBuilder().build(source, "sess-1", "scene-1")
    rendered = packet.render()
    assert "Established facts" in rendered
    assert "In play" in rendered
    assert "Chapter One" in rendered
    assert "Last session" in rendered
    assert "Immediately before now" in rendered


def test_previous_session_is_its_own_layer():
    """Summaries lose what continuity needs; the prior session stays verbatim."""
    source = _source_with(n_prior=3)
    packet = ContextBuilder().build(source, "sess-1", "scene-1")
    assert len(packet.previous) == 3
    assert "prior line 2" in packet.render()


def test_context_size_does_not_grow_with_campaign_length():
    """The load-bearing property: session 40 must cost what session 4 cost."""
    short = _source_with(n_exchanges=10, n_prior=10, n_canon=5, n_entities=5)
    long = _source_with(n_exchanges=10, n_prior=4000, n_canon=5000, n_entities=5000)
    long.summaries = [Summary(1, f"Session {i}", "Things happened." * 20)
                      for i in range(200)]

    builder = ContextBuilder()
    small = builder.build(short, "s", "sc").estimated_tokens()
    large = builder.build(long, "s", "sc").estimated_tokens()

    assert large < 14_000, f"packet grew to {large} tokens"
    assert large >= small


def test_truncation_is_reported():
    source = _source_with(n_canon=5000)
    packet = ContextBuilder().build(source, "s", "sc")
    assert packet.truncated.get("canon", 0) > 0


def test_previous_session_keeps_its_tail():
    """How a session *ended* is what you need to pick the thread back up."""
    source = _source_with(n_prior=2000)
    packet = ContextBuilder().build(source, "s", "sc")
    assert packet.previous[-1].text == "prior line 1999"


def test_history_prefers_higher_level_summaries():
    """A chapter digest is worth more per token than a scene note."""
    source = InMemoryContextSource()
    source.summaries = [
        Summary(0, f"Scene {i}", "A small thing happened. " * 60) for i in range(50)
    ] + [Summary(2, "Chapter One", "The war began.")]
    packet = ContextBuilder(budget={"history": 200}).build(source, "s", "sc")
    assert any(s.level == 2 for s in packet.history)


def test_empty_source_renders_empty():
    packet = ContextBuilder().build(InMemoryContextSource(), "s", "sc")
    assert packet.render() == ""
    assert packet.estimated_tokens() == 0
