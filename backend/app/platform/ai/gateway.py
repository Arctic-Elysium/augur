"""The AI gateway.

Nothing in the app imports an AI SDK directly. Everything goes through a
capability, and capabilities are routed to backends by config. This is the seam
that lets Golem replace Claude one capability at a time, with traffic shifting
and fallback, without touching game code.

A capability declares what it *needs* (structured output, streaming, context
size, latency tolerance). A backend declares what it *offers*. The router
refuses to bind a capability to a backend that can't satisfy it - so a
misconfiguration fails at startup, not mid-session.
"""

from __future__ import annotations

import enum
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

TSchema = TypeVar("TSchema", bound=BaseModel)


class Capability(str, enum.Enum):
    """Named units of model work. Add one here, route it in ai_routing.yaml."""

    GENERATE_WORLD = "generate_world"
    GENERATE_REGION = "generate_region"
    GENERATE_LOCATION = "generate_location"
    GENERATE_ENCOUNTER = "generate_encounter"
    NARRATE_SCENE = "narrate_scene"
    RESOLVE_TURN = "resolve_turn"       # tool-using: model calls the rules engine
    EXTRACT_ENTITIES = "extract_entities"
    SUMMARIZE = "summarize"
    GM_SUGGEST = "gm_suggest"           # human-GM mode: proposals only


@dataclass(frozen=True)
class CapabilityRequirements:
    structured_output: bool = False
    streaming: bool = False
    tool_use: bool = False
    min_context_tokens: int = 8_000
    max_latency_ms: int | None = None


REQUIREMENTS: dict[Capability, CapabilityRequirements] = {
    Capability.GENERATE_WORLD: CapabilityRequirements(
        structured_output=True, min_context_tokens=32_000
    ),
    Capability.GENERATE_REGION: CapabilityRequirements(
        structured_output=True, min_context_tokens=32_000
    ),
    Capability.GENERATE_LOCATION: CapabilityRequirements(structured_output=True),
    Capability.GENERATE_ENCOUNTER: CapabilityRequirements(structured_output=True),
    Capability.NARRATE_SCENE: CapabilityRequirements(
        streaming=True, min_context_tokens=64_000, max_latency_ms=2_000
    ),
    Capability.RESOLVE_TURN: CapabilityRequirements(
        tool_use=True, streaming=True, min_context_tokens=64_000
    ),
    Capability.EXTRACT_ENTITIES: CapabilityRequirements(structured_output=True),
    Capability.SUMMARIZE: CapabilityRequirements(min_context_tokens=128_000),
    Capability.GM_SUGGEST: CapabilityRequirements(structured_output=True),
}


@dataclass
class Message:
    role: str  # "user" | "assistant"
    content: Any


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class CompletionRequest:
    capability: Capability
    system: str
    messages: Sequence[Message]
    tools: Sequence[ToolSpec] = field(default_factory=tuple)
    response_model: type[BaseModel] | None = None
    max_tokens: int = 4096
    temperature: float | None = None
    # Correlates spend and traces back to a game session.
    session_id: str | None = None


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0

@dataclass
class CompletionResult:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    parsed: BaseModel | None = None
    usage: Usage = field(default_factory=Usage)
    backend: str = ""
    model: str = ""
    # Provider-native content blocks. A tool-use turn has to be replayed back
    # to the model verbatim - reconstructing it from `text` loses the tool_use
    # blocks and the conversation desynchronises.
    raw_content: Any = None


@dataclass(frozen=True)
class BackendCapabilities:
    structured_output: bool
    streaming: bool
    tool_use: bool
    context_tokens: int


@runtime_checkable
class AIBackend(Protocol):
    """Implemented by ClaudeBackend, OpenAICompatBackend, GolemBackend..."""

    name: str

    def capabilities(self) -> BackendCapabilities: ...

    async def complete(self, request: CompletionRequest) -> CompletionResult: ...

    async def stream(self, request: CompletionRequest) -> AsyncIterator[str]: ...

    async def health(self) -> bool: ...
