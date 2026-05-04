"""
Authentication helpers. Provides the TokenProvider Protocol plus two
implementations:

- ZitadelTokenProvider: service-account JWT-bearer flow against Zitadel,
  with in-memory token caching.
- StaticTokenProvider: returns the same bearer token on every call.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Awaitable, Callable, Protocol

import jwt
import requests

from quantumbpm.api_client import ApiClient
from quantumbpm.configuration import Configuration


class TokenProvider(Protocol):
    """Returns a valid bearer token for the next request."""

    async def get_token(self) -> str:  # pragma: no cover - protocol
        ...


class StaticTokenProvider:
    """Long-lived bearer token. Useful for Enterprise API keys and tests."""

    def __init__(self, token: str) -> None:
        self._token = token

    async def get_token(self) -> str:
        return self._token


class ZitadelTokenProvider:
    """
    Authenticates against Zitadel using a service-account JSON Key file via
    the JWT Profile (``urn:ietf:params:oauth:grant-type:jwt-bearer``) grant.
    Tokens are cached in-memory until shortly before expiry.
    """

    def __init__(
        self,
        key_file: str,
        issuer: str,
        project_id: str | None = None,
        ssl_ca_cert: str | None = None,
    ) -> None:
        with open(key_file, "r") as f:
            self._key_data = json.load(f)

        self._issuer = issuer.rstrip("/")
        self._ssl_ca_cert = ssl_ca_cert

        scopes = [
            "openid",
            "profile",
            "urn:zitadel:iam:user:resourceowner",
            "urn:zitadel:iam:org:projects:roles",
        ]
        if project_id:
            scopes.append(f"urn:zitadel:iam:org:project:id:{project_id}:aud")
        self._scope = " ".join(scopes)

        self._cached_token: str | None = None
        self._expiry: float = 0.0
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        async with self._lock:
            if self._cached_token and time.time() < self._expiry - 60:
                return self._cached_token

            token, expires_in = await asyncio.to_thread(self._exchange)
            self._cached_token = token
            self._expiry = time.time() + expires_in
            return token

    def _exchange(self) -> tuple[str, int]:
        now = int(time.time())
        assertion = jwt.encode(
            {
                "iss": self._key_data["userId"],
                "sub": self._key_data["userId"],
                "aud": self._issuer,
                "iat": now,
                "exp": now + 3600,
            },
            self._key_data["key"],
            algorithm="RS256",
            headers={"kid": self._key_data["keyId"]},
        )

        response = requests.post(
            f"{self._issuer}/oauth/v2/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "scope": self._scope,
                "assertion": assertion,
            },
            verify=self._ssl_ca_cert if self._ssl_ca_cert else True,
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        return body["access_token"], int(body["expires_in"])


def build_api_client(
    base_url: str,
    provider: TokenProvider,
    *,
    ssl_ca_cert: str | None = None,
    verify_ssl: bool = True,
) -> tuple[ApiClient, Callable[[], Awaitable[None]]]:
    """
    Build a generated ApiClient that injects the token from ``provider`` into
    each request.

    Returns the ApiClient plus a refresh coroutine the caller can invoke to
    rotate the bearer token (e.g. before a long-running worker starts).
    """
    config = Configuration(host=base_url)
    if ssl_ca_cert:
        config.ssl_ca_cert = ssl_ca_cert
    config.verify_ssl = verify_ssl

    client = ApiClient(config)

    async def refresh() -> None:
        token = await provider.get_token()
        client.set_default_header("Authorization", f"Bearer {token}")

    return client, refresh
