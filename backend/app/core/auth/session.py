"""Signed, HttpOnly cookie sessions.

Deliberately server-stateless for now: the cookie carries the principal, signed
with SESSION_SECRET. Swap for a Redis-backed store when you need revocation.
"""

from __future__ import annotations

import time
from typing import Any

from itsdangerous import BadSignature
from itsdangerous.url_safe import URLSafeTimedSerializer

from app.core.auth.oidc import OIDCPrincipal
from app.core.config.settings import Settings
from app.core.errors import AuthError

_SALT = "session.v1"


class SessionCodec:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._serializer = URLSafeTimedSerializer(settings.session_secret, salt=_SALT)

    def dump(self, principal: OIDCPrincipal) -> str:
        payload: dict[str, Any] = {
            "sub": principal.subject,
            "email": principal.email,
            "name": principal.display_name,
            "groups": list(principal.groups),
            "iat": int(time.time()),
        }
        return self._serializer.dumps(payload)

    def load(self, raw: str) -> OIDCPrincipal:
        try:
            payload = self._serializer.loads(
                raw, max_age=self._settings.session_max_age_seconds
            )
        except BadSignature as exc:
            raise AuthError("invalid session cookie") from exc
        return OIDCPrincipal(
            subject=payload["sub"],
            email=payload.get("email"),
            display_name=payload.get("name"),
            groups=tuple(payload.get("groups", [])),
        )

    def cookie_kwargs(self) -> dict[str, Any]:
        return {
            "key": self._settings.session_cookie_name,
            "httponly": True,
            "secure": self._settings.environment != "local",
            "samesite": "lax",
            "max_age": self._settings.session_max_age_seconds,
            "path": "/",
        }
