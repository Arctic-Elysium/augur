"""OpenAI-compatible backend.

Its real job right now is to prove the capability seam is honest. If adding a
second backend had required widening the protocol, the protocol was wrong - and
finding that out now is much cheaper than finding it out when Golem arrives.

Points at anything speaking the OpenAI chat-completions dialect: vLLM, Ollama,
llama.cpp, TGI, or Golem later.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.errors import UpstreamError
from app.platform.ai.gateway import (
    BackendCapabilities,
    CompletionRequest,
    CompletionResult,
    ToolCall,
    Usage,
)


class OpenAICompatBackend:
    def __init__(
        self,
        name: str,
        base_url: str,
        model: str,
        *,
        api_key: str = "",
        context_tokens: int = 32_000,
        supports_tools: bool = True,
        timeout: int = 120,
    ) -> None:
        self.name = name
        self._model = model
        self._context_tokens = context_tokens
        self._supports_tools = supports_tools
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout
        )

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            structured_output=True,
            streaming=True,
            tool_use=self._supports_tools,
            context_tokens=self._context_tokens,
        )

    def _payload(self, request: CompletionRequest, *, stream: bool) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [{"role": "system", "content": request.system}]
        for message in request.messages:
            content = message.content
            messages.append({
                "role": message.role,
                "content": content if isinstance(content, str) else json.dumps(content),
            })

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "stream": stream,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.tools and self._supports_tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in request.tools
            ]
        return payload

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        response = await self._http.post(
            "/chat/completions", json=self._payload(request, stream=False)
        )
        if response.status_code != 200:
            raise UpstreamError(f"{self.name} returned {response.status_code}")

        body = response.json()
        choice = body["choices"][0]["message"]
        text = choice.get("content") or ""

        tool_calls = [
            ToolCall(
                id=call.get("id", ""),
                name=call["function"]["name"],
                arguments=json.loads(call["function"].get("arguments") or "{}"),
            )
            for call in (choice.get("tool_calls") or [])
        ]

        parsed = None
        if request.response_model is not None:
            parsed = request.response_model.model_validate_json(_strip_fences(text))

        usage = body.get("usage") or {}
        return CompletionResult(
            text=text,
            tool_calls=tool_calls,
            parsed=parsed,
            usage=Usage(
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
            ),
            backend=self.name,
            model=self._model,
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[str]:
        async with self._http.stream(
            "POST", "/chat/completions", json=self._payload(request, stream=True)
        ) as response:
            if response.status_code != 200:
                raise UpstreamError(f"{self.name} returned {response.status_code}")
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line.removeprefix("data: ").strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0]["delta"]
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                chunk = delta.get("content")
                if chunk:
                    yield chunk

    async def health(self) -> bool:
        try:
            response = await self._http.get("/models", timeout=5)
            return response.status_code == 200
        except Exception:
            return False

    async def aclose(self) -> None:
        await self._http.aclose()


def _strip_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        cleaned = cleaned.rsplit("```", 1)[0]
    return cleaned.strip()
