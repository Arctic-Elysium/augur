from __future__ import annotations

from fastapi import APIRouter


class NarrativeModule:
    name = "narrative"

    def router(self) -> APIRouter | None:
        # Headless. The turn loop is invoked by the sessions module rather
        # than exposed directly - a turn is only meaningful inside a session.
        return None

    def import_models(self) -> None:
        try:
            from app.modules.narrative import models  # noqa: F401
        except ImportError:
            pass
