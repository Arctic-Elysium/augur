from __future__ import annotations

from fastapi import APIRouter


class CampaignsModule:
    name = "campaigns"

    def router(self) -> APIRouter | None:
        from app.modules.campaigns.router import router

        return router

    def import_models(self) -> None:
        from app.modules.campaigns import models  # noqa: F401
