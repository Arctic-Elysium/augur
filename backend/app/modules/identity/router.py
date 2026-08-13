from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse

from app.core.auth.deps import DbDep, PrincipalDep, SettingsDep
from app.core.errors import AuthError
from app.modules.identity.service import IdentityService

router = APIRouter()

_STATE_COOKIE = "oidc_txn"


@router.get("/login")
async def login(request: Request, settings: SettingsDep) -> RedirectResponse:
    oidc = request.app.state.oidc
    auth_request = await oidc.build_authorization_request()

    response = RedirectResponse(auth_request.url, status_code=302)
    # Transaction state is short-lived and separate from the session cookie.
    txn = request.app.state.session_codec._serializer.dumps(
        {
            "state": auth_request.state,
            "nonce": auth_request.nonce,
            "verifier": auth_request.code_verifier,
        }
    )
    response.set_cookie(
        _STATE_COOKIE,
        txn,
        httponly=True,
        secure=settings.environment != "local",
        samesite="lax",
        max_age=600,
        path="/",
    )
    return response


@router.get("/callback")
async def callback(
    request: Request, db: DbDep, settings: SettingsDep, code: str = "", state: str = ""
) -> RedirectResponse:
    raw_txn = request.cookies.get(_STATE_COOKIE)
    if not raw_txn or not code:
        raise AuthError("missing authorization transaction")

    txn = request.app.state.session_codec._serializer.loads(raw_txn, max_age=600)
    if txn["state"] != state:
        raise AuthError("state mismatch")

    principal = await request.app.state.oidc.exchange_code(
        code=code, code_verifier=txn["verifier"], nonce=txn["nonce"]
    )
    await IdentityService(db).upsert_from_principal(principal)

    response = RedirectResponse(settings.frontend_url, status_code=302)
    response.delete_cookie(_STATE_COOKIE, path="/")
    codec = request.app.state.session_codec
    response.set_cookie(value=codec.dump(principal), **codec.cookie_kwargs())
    return response


@router.post("/logout")
async def logout(request: Request, settings: SettingsDep) -> Response:
    response = Response(status_code=204)
    response.delete_cookie(settings.session_cookie_name, path="/")
    return response


@router.get("/me")
async def me(principal: PrincipalDep, settings: SettingsDep) -> dict:
    return {
        "subject": principal.subject,
        "email": principal.email,
        "displayName": principal.display_name,
        "groups": list(principal.groups),
        "isAdmin": principal.in_group(settings.oidc_admin_group),
    }
