from __future__ import annotations

from fastapi import APIRouter


class CharactersModule:
    name = "characters"

    def router(self) -> APIRouter | None:
        from app.modules.characters.router import router

        return router

    def import_models(self) -> None:
        try:
            from app.modules.characters import models  # noqa: F401
        except ImportError:
            pass
