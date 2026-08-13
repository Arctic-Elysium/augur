from __future__ import annotations

from fastapi import APIRouter


class RulesModule:
    name = "rules"

    def router(self) -> APIRouter | None:
        from app.modules.rules.router import router

        return router

    def import_models(self) -> None:
        # The rules engine is pure - no persistence of its own. Characters and
        # check ledgers are owned by the characters and sessions modules.
        pass
