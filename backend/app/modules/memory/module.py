from __future__ import annotations

from fastapi import APIRouter


class MemoryModule:
    name = "memory"

    def router(self) -> APIRouter | None:
        from app.modules.memory.router import router

        return router

    def import_models(self) -> None:
        from app.modules.memory import models  # noqa: F401
