from __future__ import annotations

from fastapi import APIRouter


class MemoryModule:
    name = "memory"

    def router(self) -> APIRouter | None:
        # Milestone 0: registered but not yet exposed.
        return None

    def import_models(self) -> None:
        try:
            from app.modules.memory import models  # noqa: F401
        except ImportError:
            pass
