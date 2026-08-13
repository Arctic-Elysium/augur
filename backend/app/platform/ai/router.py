"""Capability -> backend routing, with weights, fallback and budget enforcement."""

from __future__ import annotations

import random
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace

import yaml
from pydantic import ValidationError as PydanticValidationError

from app.core.errors import BudgetExceeded, InvalidRequest, UpstreamError
from app.platform.ai.gateway import (
    REQUIREMENTS,
    AIBackend,
    Capability,
    CompletionRequest,
    CompletionResult,
    Message,
)
from app.platform.observability.metrics import ai_latency, ai_requests, ai_tokens


@dataclass
class Route:
    backend: str
    model: str
    weight: int = 100
    fallback: str | None = None


class TokenLedger:
    """Per-session spend cap. Cheap in-memory guard; persist later if needed."""

    def __init__(self, budget: int) -> None:
        self._budget = budget
        self._spent: dict[str, int] = {}

    def check(self, session_id: str | None) -> None:
        if session_id and self._spent.get(session_id, 0) >= self._budget:
            raise BudgetExceeded(f"session {session_id} exceeded token budget")

    def record(self, session_id: str | None, tokens: int) -> None:
        if session_id:
            self._spent[session_id] = self._spent.get(session_id, 0) + tokens

    def spent(self, session_id: str) -> int:
        return self._spent.get(session_id, 0)


class AIRouter:
    def __init__(
        self,
        backends: dict[str, AIBackend],
        routes: dict[Capability, list[Route]],
        ledger: TokenLedger,
    ) -> None:
        self._backends = backends
        self._routes = routes
        self._ledger = ledger
        self._validate()

    @classmethod
    def from_config(
        cls, path: str, backends: dict[str, AIBackend], ledger: TokenLedger
    ) -> AIRouter:
        with open(path) as fh:
            raw = yaml.safe_load(fh) or {}
        routes: dict[Capability, list[Route]] = {}
        for cap_name, entries in (raw.get("capabilities") or {}).items():
            cap = Capability(cap_name)
            routes[cap] = [Route(**entry) for entry in entries]
        return cls(backends, routes, ledger)

    def _validate(self) -> None:
        """Fail at startup, not mid-session."""
        for cap, routes in self._routes.items():
            need = REQUIREMENTS[cap]
            for route in routes:
                backend = self._backends.get(route.backend)
                if backend is None:
                    raise ValueError(f"{cap.value} routes to unknown backend '{route.backend}'")
                have = backend.capabilities()
                if need.structured_output and not have.structured_output:
                    raise ValueError(f"{route.backend} cannot satisfy structured output for {cap.value}")
                if need.streaming and not have.streaming:
                    raise ValueError(f"{route.backend} cannot satisfy streaming for {cap.value}")
                if need.tool_use and not have.tool_use:
                    raise ValueError(f"{route.backend} cannot satisfy tool use for {cap.value}")
                if have.context_tokens < need.min_context_tokens:
                    raise ValueError(
                        f"{route.backend} context {have.context_tokens} < "
                        f"{need.min_context_tokens} required by {cap.value}"
                    )

    def _pick(self, capability: Capability) -> Route:
        routes = self._routes.get(capability)
        if not routes:
            raise UpstreamError(f"no backend routed for capability {capability.value}")
        if len(routes) == 1:
            return routes[0]
        # Weighted choice enables gradual traffic shifting onto a new backend.
        total = sum(r.weight for r in routes)
        pick = random.uniform(0, total)
        cursor = 0.0
        for route in routes:
            cursor += route.weight
            if pick <= cursor:
                return route
        return routes[-1]

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        self._ledger.check(request.session_id)
        route = self._pick(request.capability)
        capability = request.capability.value

        started = time.perf_counter()
        backend_used = route.backend
        try:
            try:
                result = await self._backends[route.backend].complete(request)
            except Exception:
                if not route.fallback:
                    ai_requests.labels(capability, route.backend, "error").inc()
                    raise
                ai_requests.labels(capability, route.backend, "fallback").inc()
                backend_used = route.fallback
                result = await self._backends[route.fallback].complete(request)
        finally:
            ai_latency.labels(capability, backend_used).observe(
                time.perf_counter() - started
            )

        ai_requests.labels(capability, backend_used, "ok").inc()
        ai_tokens.labels(capability, backend_used, "input").inc(
            result.usage.input_tokens
        )
        ai_tokens.labels(capability, backend_used, "output").inc(
            result.usage.output_tokens
        )
        ai_tokens.labels(capability, backend_used, "cache_write").inc(
            result.usage.cache_write_tokens
        )
        ai_tokens.labels(capability, backend_used, "cache_read").inc(
            result.usage.cache_read_tokens
        )
        self._ledger.record(
            request.session_id, result.usage.input_tokens + result.usage.output_tokens
        )
        return result

    async def complete_structured(
        self, request: CompletionRequest, *, attempts: int = 2
    ) -> CompletionResult:
        """Structured output with bounded retry.

        A validation failure is fed back to the model as a correction rather
        than raised, because the usual cause is a stray prose preamble the
        model will happily drop when told. Two attempts, then fail loudly - an
        unbounded retry on a genuinely confused model burns budget silently.
        """
        if request.response_model is None:
            raise InvalidRequest("complete_structured requires a response_model")

        messages = list(request.messages)
        last_error: Exception | None = None

        for attempt in range(attempts):
            attempt_request = replace(request, messages=messages)
            try:
                result = await self.complete(attempt_request)
                if result.parsed is None:
                    raise UpstreamError("backend returned no parsed payload")
                return result
            except (PydanticValidationError, UpstreamError, ValueError) as exc:
                last_error = exc
                ai_requests.labels(
                    request.capability.value, "*", "invalid_structure"
                ).inc()
                if attempt == attempts - 1:
                    break
                messages = [
                    *messages,
                    Message(role="assistant", content="(invalid output)"),
                    Message(
                        role="user",
                        content=(
                            f"That did not match the required schema: {exc}. "
                            "Return only the JSON object, with no preamble, "
                            "commentary, or code fences."
                        ),
                    ),
                ]

        raise UpstreamError(
            f"structured output failed after {attempts} attempts: {last_error}"
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[str]:
        self._ledger.check(request.session_id)
        route = self._pick(request.capability)
        capability = request.capability.value

        started = time.perf_counter()
        first_token_seen = False
        try:
            async for chunk in self._backends[route.backend].stream(request):
                if not first_token_seen:
                    # Time to first token is what the player actually feels.
                    ai_latency.labels(capability, route.backend).observe(
                        time.perf_counter() - started
                    )
                    first_token_seen = True
                yield chunk
        except Exception:
            ai_requests.labels(capability, route.backend, "error").inc()
            raise
        ai_requests.labels(capability, route.backend, "ok").inc()

    async def health(self) -> dict[str, bool]:
        """Readiness detail. A backend that is routed but unreachable should
        surface here rather than at a player's first turn."""
        return {name: await b.health() for name, b in self._backends.items()}
