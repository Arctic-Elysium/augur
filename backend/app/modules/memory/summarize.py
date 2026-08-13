"""Session summarisation - the compression rung of the ladder.

Without this, `previous_session` loads the prior session verbatim and the
context cost grows with how long you played last time. With it, everything
older than the current session collapses to a paragraph, and the packet stays
flat no matter how many sessions have gone by.

Runs on the cheap model: this is compression, not composition.
"""

from __future__ import annotations

from app.modules.memory.models import SummaryLevel
from app.modules.memory.service import MemoryService
from app.platform.ai.context import Exchange
from app.platform.ai.gateway import Capability, CompletionRequest, Message
from app.platform.ai.prompts import render_prompt
from app.platform.ai.router import AIRouter

# Sessions above this many exchanges are summarised from the ends rather than
# the whole: the opening establishes and the close resolves, and the middle of
# a long session is mostly texture the summary would drop anyway.
HEAD_TAIL = 60


class Summarizer:
    def __init__(self, ai: AIRouter, memory: MemoryService) -> None:
        self._ai = ai
        self._memory = memory

    async def summarize_session(
        self, *, session_id: str, session_number: int, exchanges: list[Exchange]
    ) -> str:
        if not exchanges:
            return ""

        if len(exchanges) > HEAD_TAIL * 2:
            sample = (
                exchanges[:HEAD_TAIL]
                + [Exchange("—", "[middle of session omitted]")]
                + exchanges[-HEAD_TAIL:]
            )
        else:
            sample = exchanges

        transcript = "\n".join(f"{e.speaker}: {e.text}" for e in sample)
        system, _ = render_prompt("summarize")

        try:
            result = await self._ai.complete(
                CompletionRequest(
                    capability=Capability.SUMMARIZE,
                    system=system,
                    messages=[Message(role="user", content=transcript)],
                    max_tokens=700,
                    session_id=session_id,
                )
            )
        except Exception:
            # A failed summary must not block ending a session. The transcript
            # is still on disk and can be summarised later.
            return ""

        body = result.text.strip()
        if body:
            await self._memory.add_summary(
                level=SummaryLevel.SESSION,
                body=body,
                title=f"Session {session_number}",
                session_number=session_number,
            )
        return body
