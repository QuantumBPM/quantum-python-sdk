"""QuantumBPM top-level entry point."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from quantumbpm.api_client import ApiClient
from quantumbpm.auth import TokenProvider, build_api_client
from quantumbpm.bpmn import BpmnClient
from quantumbpm.dmn import DmnClient
from quantumbpm.workers import (
    DEFAULT_MAX_ERROR_MESSAGE_BYTES,
    DEFAULT_SHUTDOWN_TIMEOUT_S,
    Worker,
)


class QuantumBPM:
    """
    Top-level QuantumBPM SDK entry point. Construct one and reach the
    sub-clients via ``.dmn``, ``.bpmn``, and ``.new_worker(...)``.

    Example::

        async with QuantumBPM(
            base_url="https://api.quantumbpm.com",
            project_id="00000000-0000-0000-0000-000000000000",
            token_provider=ZitadelTokenProvider(...),
        ) as client:
            result = await client.dmn.evaluate("loan-eligibility", vars)
    """

    def __init__(
        self,
        *,
        base_url: str,
        project_id: str | UUID,
        token_provider: TokenProvider,
        ssl_ca_cert: str | None = None,
        verify_ssl: bool = True,
    ) -> None:
        if not base_url:
            raise ValueError("quantumbpm: base_url is required")
        if not project_id:
            raise ValueError("quantumbpm: project_id is required")
        if token_provider is None:
            raise ValueError("quantumbpm: token_provider is required")

        self._token_provider = token_provider
        self._project_id = UUID(project_id) if isinstance(project_id, str) else project_id

        self._api, self._refresh = build_api_client(
            base_url,
            token_provider,
            ssl_ca_cert=ssl_ca_cert,
            verify_ssl=verify_ssl,
        )

        self.dmn = DmnClient(self._api, self._project_id, refresh=self._refresh)
        self.bpmn = BpmnClient(self._api, self._project_id, refresh=self._refresh)

    @property
    def project_id(self) -> UUID:
        return self._project_id

    @property
    def raw(self) -> ApiClient:
        """
        Underlying generated ApiClient. Use for endpoints not yet wrapped.
        Calls through it send the token of the last SDK call or
        :meth:`authenticate`, so await :meth:`authenticate` first.
        """
        return self._api

    async def authenticate(self) -> None:
        """
        Put a token from the TokenProvider on the ApiClient. The sub-clients
        and workers do this before every API call.
        """
        await self._refresh()

    def new_worker(
        self,
        *,
        client_id: str | None = None,
        max_error_message_bytes: int = DEFAULT_MAX_ERROR_MESSAGE_BYTES,
        shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_S,
    ) -> Worker:
        """
        Construct an external job worker bound to this client's project.
        Register handlers via ``@worker.handler('task-type')`` and call
        ``await worker.run(stop_event)``.
        """
        return Worker(
            self._api,
            self._project_id,
            client_id=client_id,
            max_error_message_bytes=max_error_message_bytes,
            shutdown_timeout=shutdown_timeout,
            refresh=self._refresh,
        )

    async def __aenter__(self) -> "QuantumBPM":
        await self.authenticate()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        # No explicit teardown for the urllib3-based ApiClient.
        return None
