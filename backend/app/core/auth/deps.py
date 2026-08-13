from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.oidc import OIDCPrincipal
from app.core.config.settings import Settings, get_settings
from app.core.db.engine import session_scope
from app.core.errors import AuthError, ForbiddenError

SettingsDep = Annotated[Settings, Depends(get_settings)]
DbDep = Annotated[AsyncSession, Depends(session_scope)]


async def current_principal(request: Request, settings: SettingsDep) -> OIDCPrincipal:
    raw = request.cookies.get(settings.session_cookie_name)
    if not raw:
        raise AuthError("no session cookie")
    codec = request.app.state.session_codec
    return codec.load(raw)


PrincipalDep = Annotated[OIDCPrincipal, Depends(current_principal)]


async def require_admin(
    principal: PrincipalDep, settings: SettingsDep
) -> OIDCPrincipal:
    if not principal.in_group(settings.oidc_admin_group):
        raise ForbiddenError("admin group required")
    return principal


AdminDep = Annotated[OIDCPrincipal, Depends(require_admin)]
