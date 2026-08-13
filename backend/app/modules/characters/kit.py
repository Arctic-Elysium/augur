"""Starting kit inference.

A player who writes "she left the harbour with her father's knife and nothing
else" has already told us what she carries. Making them type it again into an
inventory box is asking them to do data entry on their own fiction.

Conservative on purpose: a handful of mundane, personal items. This is not a
shopping trip, and an inferred magic sword would be the model handing out
treasure nobody agreed to.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.platform.ai.gateway import Capability, CompletionRequest, Message
from app.platform.ai.router import AIRouter

MAX_ITEMS = 6

_SYSTEM = """\
From the character description below, list what this person plausibly carries.

Rules:
- Mundane and personal only. Clothes, tools of their trade, a keepsake, a
  weapon if the description implies one.
- Nothing valuable, magical, or advantageous. No money beyond "a few coins".
- Only what the description actually supports. If it says nothing about
  equipment, return a short list of ordinary essentials.
- Short noun phrases. "a worn leather satchel", not "Worn Leather Satchel +1".
- At most six.

Return JSON only: {"items": ["...", "..."]}
"""


class Kit(BaseModel):
    items: list[str] = Field(default_factory=list)


class StartingKit:
    def __init__(self, ai: AIRouter) -> None:
        self._ai = ai

    async def infer(self, *, name: str, backstory: str, hooks: list[dict]) -> list[str]:
        """Never raises - a character with no starting kit is fine, a character
        creation that 500s because of a nice-to-have is not."""
        threads = "\n".join(
            f"- {h.get('kind')}: {h.get('subject')} {h.get('detail', '')}".strip()
            for h in hooks
            if h.get("subject")
        )
        description = f"{name}\n\n{backstory}\n\n{threads}".strip()

        try:
            result = await self._ai.complete_structured(
                CompletionRequest(
                    capability=Capability.EXTRACT_ENTITIES,
                    system=_SYSTEM,
                    messages=[Message(role="user", content=description)],
                    response_model=Kit,
                    max_tokens=400,
                )
            )
        except Exception:
            return []

        kit = result.parsed if isinstance(result.parsed, Kit) else Kit()
        return [i.strip() for i in kit.items if i.strip()][:MAX_ITEMS]
