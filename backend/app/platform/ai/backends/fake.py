"""Scripted backend for tests.

Makes the entire gateway testable without network or spend. A gateway you can
only exercise by paying for it is a gateway that stops being exercised.

Also useful for reproducing a specific model behaviour when chasing a bug:
script the exact sequence of tool calls that caused it and replay.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.platform.ai.gateway import (
    BackendCapabilities,
    CompletionRequest,
    CompletionResult,
    ToolCall,
    Usage,
)


def _strip_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        cleaned = cleaned.rsplit("```", 1)[0]
    return cleaned.strip()


class FakeBackend:
    """Responses may be plain strings or full CompletionResult objects.

    Strings cover the common case; results are for when a test needs to script
    tool calls or specific token usage.
    """

    def __init__(
        self,
        name: str = "fake",
        *,
        responses: list | None = None,
        capabilities: BackendCapabilities | None = None,
        fail: bool = False,
        fail_with: Exception | None = None,
        usage: Usage | None = None,
    ) -> None:
        self.name = name
        self._responses: list = list(responses or [])
        self._capabilities = capabilities or BackendCapabilities(
            structured_output=True, streaming=True,
            tool_use=True, context_tokens=200_000,
        )
        self._fail_with = fail_with or (
            RuntimeError("fake backend failure") if fail else None
        )
        self._usage = usage or Usage(input_tokens=100, output_tokens=50)
        self.calls: list[CompletionRequest] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def capabilities(self) -> BackendCapabilities:
        return self._capabilities

    def queue(self, *, text: str = "", tool_calls: list[ToolCall] | None = None) -> None:
        self._responses.append(
            CompletionResult(
                text=text, tool_calls=tool_calls or [], usage=self._usage,
                backend=self.name, model="fake-1",
            )
        )

    def _next(self, request: CompletionRequest) -> CompletionResult:
        if not self._responses:
            return CompletionResult(
                text="", usage=self._usage, backend=self.name, model="fake-1"
            )
        nxt = self._responses.pop(0)
        if isinstance(nxt, CompletionResult):
            return nxt

        # Parse here rather than in the router, mirroring what a real backend
        # does - so the retry path is exercised the same way in tests as in
        # production.
        parsed = None
        if request.response_model is not None:
            parsed = request.response_model.model_validate_json(_strip_fences(nxt))

        return CompletionResult(
            text=nxt, parsed=parsed, usage=self._usage,
            backend=self.name, model="fake-1",
        )

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        self.calls.append(request)
        if self._fail_with is not None:
            raise self._fail_with
        return self._next(request)

    async def stream(self, request: CompletionRequest) -> AsyncIterator[str]:
        self.calls.append(request)
        if self._fail_with is not None:
            raise self._fail_with
        result = self._next(request)
        for word in result.text.split(" "):
            if word:
                yield word + " "

    async def health(self) -> bool:
        return self._fail_with is None
