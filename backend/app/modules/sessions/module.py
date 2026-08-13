from __future__ import annotations

from fastapi import APIRouter


class SessionsModule:
    name = "sessions"

    def router(self) -> APIRouter | None:
        from app.modules.sessions.router import router

        return router

    def import_models(self) -> None:
        try:
            from app.modules.sessions import models  # noqa: F401
        except ImportError:
            pass
