"""Voidauth OIDC client.

Ported from the Tome/Cairn auth layer. Two invariants carried over from the
Tome security review, do not relax them:

1. Identity is keyed on the `sub` claim, never on `email`. An IdP-supplied
   email is not an identity and must never grant elevated access.
2. Authorization derives from the groups claim on a *verified* ID token.
   Never trust group membership sent by the client.
"""

from __future__ import annotations

from collections.abc import Sequence

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any

import httpx
from jose import jwt
from jose.exceptions import JWTError

from app.core.config.settings import Settings
from app.core.errors import AuthError


@dataclass(frozen=True)
class OIDCPrincipal:
    """A verified end user. `subject` is the stable identity key."""

    subject: str
    email: str | None
    display_name: str | None
    groups: tuple[str, ...]

    def in_group(self, group: str) -> bool:
        return group in self.groups

    def in_any_group(self, groups: "Sequence[str]") -> bool:
        """Membership of any one of several groups.

        Admin rights are configured as a list because the group that already
        exists in the IdP is rarely the name an app would have chosen.
        """
        return any(g in self.groups for g in groups)


@dataclass(frozen=True)
class AuthorizationRequest:
    url: str
    state: str
    nonce: str
    code_verifier: str


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class OIDCClient:
    """Lazily discovers provider metadata and caches JWKS."""

    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http
        self._metadata: dict[str, Any] | None = None
        self._jwks: dict[str, Any] | None = None
        self._jwks_fetched_at: float = 0.0

    # --- discovery ---

    async def metadata(self) -> dict[str, Any]:
        if self._metadata is None:
            url = f"{self._settings.oidc_issuer.rstrip('/')}/.well-known/openid-configuration"
            resp = await self._http.get(url, timeout=10)
            resp.raise_for_status()
            self._metadata = resp.json()
        return self._metadata

    async def _jwks_keys(self) -> dict[str, Any]:
        # Refresh hourly so key rotation doesn't require a restart.
        if self._jwks is None or time.time() - self._jwks_fetched_at > 3600:
            meta = await self.metadata()
            resp = await self._http.get(meta["jwks_uri"], timeout=10)
            resp.raise_for_status()
            self._jwks = resp.json()
            self._jwks_fetched_at = time.time()
        return self._jwks

    # --- authorization code + PKCE ---

    async def build_authorization_request(self) -> AuthorizationRequest:
        meta = await self.metadata()
        verifier = _b64url(secrets.token_bytes(64))
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)

        params = {
            "response_type": "code",
            "client_id": self._settings.oidc_client_id,
            "redirect_uri": self._redirect_uri,
            "scope": " ".join(self._settings.oidc_scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        query = "&".join(f"{k}={httpx.QueryParams({k: v})[k]}" for k, v in params.items())
        return AuthorizationRequest(
            url=f"{meta['authorization_endpoint']}?{query}",
            state=state,
            nonce=nonce,
            code_verifier=verifier,
        )

    async def exchange_code(
        self, *, code: str, code_verifier: str, nonce: str
    ) -> OIDCPrincipal:
        meta = await self.metadata()
        resp = await self._http.post(
            meta["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._redirect_uri,
                "client_id": self._settings.oidc_client_id,
                "client_secret": self._settings.oidc_client_secret,
                "code_verifier": code_verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        if resp.status_code != 200:
            raise AuthError("token exchange failed")

        id_token = resp.json().get("id_token")
        if not id_token:
            raise AuthError("provider returned no id_token")
        return await self.verify_id_token(id_token, expected_nonce=nonce)

    async def verify_id_token(
        self, id_token: str, *, expected_nonce: str | None = None
    ) -> OIDCPrincipal:
        meta = await self.metadata()
        keys = await self._jwks_keys()
        try:
            claims = jwt.decode(
                id_token,
                keys,
                algorithms=["RS256", "ES256"],
                audience=self._settings.oidc_client_id,
                issuer=meta["issuer"],
                options={"verify_at_hash": False},
            )
        except JWTError as exc:
            raise AuthError(f"id_token verification failed: {exc}") from exc

        if expected_nonce is not None and claims.get("nonce") != expected_nonce:
            raise AuthError("nonce mismatch")

        subject = claims.get("sub")
        if not subject:
            raise AuthError("id_token has no sub claim")

        raw_groups = claims.get(self._settings.oidc_groups_claim) or []
        if isinstance(raw_groups, str):
            raw_groups = [raw_groups]

        return OIDCPrincipal(
            subject=subject,
            email=claims.get("email"),
            display_name=claims.get("name") or claims.get("preferred_username"),
            groups=tuple(str(g) for g in raw_groups),
        )

    @property
    def _redirect_uri(self) -> str:
        return f"{self._settings.base_url.rstrip('/')}{self._settings.oidc_redirect_path}"
