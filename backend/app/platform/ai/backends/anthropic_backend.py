from __future__ import annotations

from collections.abc import AsyncIterator

from anthropic import AsyncAnthropic

from app.platform.ai.gateway import (
    AIBackend,
    BackendCapabilities,
    CompletionRequest,
    CompletionResult,
    ToolCall,
    Usage,
)


class AnthropicBackend(AIBackend):
    name = "claude"

    def __init__(self, api_key: str, model: str, timeout: int = 120) -> None:
        self._client = AsyncAnthropic(api_key=api_key, timeout=timeout)
        self._model = model

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            structured_output=True,
            streaming=True,
            tool_use=True,
            context_tokens=200_000,
        )

    def _payload(self, request: CompletionRequest) -> dict:
        """Build the request, marking the stable prefix as cacheable.

        A turn makes at least two calls, and every call re-sends the same
        system prompt and the same twelve tool schemas. Uncached, that prefix
        is billed in full each time and dominates the cost of a turn - the
        variable part (what the player typed) is tiny by comparison.

        A cache breakpoint on the last tool definition covers tools AND system,
        since the cached prefix runs tools -> system -> messages. Reads off a
        warm cache are a fraction of the input rate, so the second round of a
        turn costs far less than the first, and consecutive turns within the
        cache window ride the same prefix.
        """
        system: list[dict] | str = request.system
        tools: list[dict] | None = None

        if request.tools:
            tools = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                }
                for t in request.tools
            ]
            # Breakpoint on the final tool: everything above it is cached.
            tools[-1]["cache_control"] = {"type": "ephemeral"}
        elif request.system:
            # No tools, so the system prompt carries the breakpoint itself.
            system = [
                {
                    "type": "text",
                    "text": request.system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]

        payload: dict = {
            "model": self._model,
            "max_tokens": request.max_tokens,
            "system": system,
            "messages": [
                {"role": m.role, "content": m.content} for m in request.messages
            ],
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if tools is not None:
            payload["tools"] = tools
        return payload

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        response = await self._client.messages.create(**self._payload(request))

        text = "".join(
            block.text for block in response.content if block.type == "text"
        )
        tool_calls = [
            ToolCall(id=block.id, name=block.name, arguments=block.input)
            for block in response.content
            if block.type == "tool_use"
        ]

        parsed = None
        if request.response_model is not None:
            parsed = request.response_model.model_validate_json(_strip_fences(text))

        return CompletionResult(
            text=text,
            tool_calls=tool_calls,
            raw_content=[block.model_dump() for block in response.content],
            parsed=parsed,
            usage=Usage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                cache_write_tokens=getattr(
                    response.usage, "cache_creation_input_tokens", 0
                ) or 0,
                cache_read_tokens=getattr(
                    response.usage, "cache_read_input_tokens", 0
                ) or 0,
            ),
            backend=self.name,
            model=self._model,
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[str]:
        async with self._client.messages.stream(**self._payload(request)) as stream:
            async for chunk in stream.text_stream:
                yield chunk

    async def health(self) -> bool:
        try:
            await self._client.messages.create(
                model=self._model,
                max_tokens=1,
                messages=[{"role": "user", "content": "ok"}],
            )
            return True
        except Exception:
            return False


def _strip_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        cleaned = cleaned.rsplit("```", 1)[0]
    return cleaned.strip()
