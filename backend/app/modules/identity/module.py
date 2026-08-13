from __future__ import annotations

from fastapi import APIRouter


class IdentityModule:
    name = "auth"

    def router(self) -> APIRouter:
        from app.modules.identity.router import router

        return router

    def import_models(self) -> None:
        from app.modules.identity import models  # noqa: F401
