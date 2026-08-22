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


def is_platform_admin(principal: OIDCPrincipal, settings: Settings) -> bool:
    """Derived from the verified token, never from a stored flag.

    A database column saying "this user is an admin" outlives the IdP group
    that justified it: revoke someone in Voidauth and the row keeps letting
    them in. Re-reading the claim means access follows the directory.
    """
    return principal.in_any_group(settings.oidc_admin_groups)


async def require_admin(
    principal: PrincipalDep, settings: SettingsDep
) -> OIDCPrincipal:
    if not is_platform_admin(principal, settings):
        raise ForbiddenError("admin group required")
    return principal


async def admin_flag(principal: PrincipalDep, settings: SettingsDep) -> bool:
    """Admin-ness as a plain bool, for endpoints that widen rather than gate."""
    return is_platform_admin(principal, settings)


AdminFlagDep = Annotated[bool, Depends(admin_flag)]


AdminDep = Annotated[OIDCPrincipal, Depends(require_admin)]
