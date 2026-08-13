from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.oidc import OIDCPrincipal
from app.modules.identity.models import User


class IdentityService:
    """Maps a verified OIDC principal onto a local user row."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def upsert_from_principal(self, principal: OIDCPrincipal) -> User:
        result = await self._db.execute(
            select(User).where(User.subject == principal.subject)
        )
        user = result.scalar_one_or_none()
        now = datetime.now(timezone.utc)

        if user is None:
            user = User(subject=principal.subject)
            self._db.add(user)

        user.email = principal.email
        user.display_name = principal.display_name
        user.last_groups = list(principal.groups)
        user.last_seen_at = now
        await self._db.flush()
        return user

    async def get_by_subject(self, subject: str) -> User | None:
        result = await self._db.execute(select(User).where(User.subject == subject))
        return result.scalar_one_or_none()
