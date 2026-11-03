"""
Authentication helpers: the TokenProvider Protocol, ClientCredentialsTokenProvider
for any OIDC provider, and StaticTokenProvider for a fixed bearer token.
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, Protocol, Sequence

import requests

from quantumbpm.api_client import ApiClient
from quantumbpm.configuration import Configuration
from quantumbpm.exceptions import ApiException

_REFRESH_MARGIN_S = 60.0
_DEFAULT_LIFETIME_S = 300.0


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


class ClientCredentialsTokenProvider:
    """
    Fetches access tokens from any OIDC provider with the OAuth 2.0 client
    credentials grant, caching each until less than 60 seconds of it remain.
    """

    def __init__(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        scopes: Sequence[str] | None = None,
        audience: str | None = None,
        *,
        ssl_ca_cert: str | None = None,
    ) -> None:
        if not token_url or not client_id or not client_secret:
            raise ValueError("quantumbpm: token_url, client_id and client_secret are required")

        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = " ".join(scopes) if scopes else None
        self._audience = audience or None
        self._ssl_ca_cert = ssl_ca_cert

        self._cached_token: str | None = None
        self._expiry: float = 0.0
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        async with self._lock:
            if self._cached_token and time.time() < self._expiry - _REFRESH_MARGIN_S:
                return self._cached_token

            requested_at = time.time()
            token, expires_in = await asyncio.to_thread(self._request_token)
            self._cached_token = token
            self._expiry = requested_at + expires_in
            return token

    def _request_token(self) -> tuple[str, float]:
        data = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        if self._scope:
            data["scope"] = self._scope
        if self._audience:
            data["audience"] = self._audience

        response = requests.post(
            self._token_url,
            data=data,
            headers={"Accept": "application/json"},
            verify=self._ssl_ca_cert if self._ssl_ca_cert else True,
            timeout=30,
        )
        if not 200 <= response.status_code < 300:
            raise ApiException(
                status=response.status_code,
                reason=f"token request failed: {response.reason}",
                body=response.text,
            )

        body = response.json()
        token = body.get("access_token")
        if not token:
            raise ApiException(
                status=response.status_code,
                reason="token response has no access_token",
                body=response.text,
            )
        expires_in = body.get("expires_in")
        return token, _DEFAULT_LIFETIME_S if expires_in is None else float(expires_in)


def build_api_client(
    base_url: str,
    provider: TokenProvider,
    *,
    ssl_ca_cert: str | None = None,
    verify_ssl: bool = True,
) -> tuple[ApiClient, Callable[[], Awaitable[None]]]:
    """
    Build a generated ApiClient plus a refresh coroutine that sets its
    Authorization header to a token from ``provider``. The SDK clients and
    workers await the refresh before every API call, so the provider decides
    when a new token is fetched.
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
