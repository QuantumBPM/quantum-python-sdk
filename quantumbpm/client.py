"""QuantumBPM top-level entry point."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from quantumbpm.api_client import ApiClient
from quantumbpm.auth import TokenProvider, build_api_client
from quantumbpm.bpmn import BpmnClient
from quantumbpm.dmn import DmnClient
from quantumbpm.workers import Worker


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

        self.dmn = DmnClient(self._api, self._project_id)
        self.bpmn = BpmnClient(self._api, self._project_id)

    @property
    def project_id(self) -> UUID:
        return self._project_id

    @property
    def raw(self) -> ApiClient:
        """Underlying generated ApiClient. Use for endpoints not yet wrapped."""
        return self._api

    async def authenticate(self) -> None:
        """Acquire a fresh bearer token from the configured TokenProvider."""
        await self._refresh()

    def new_worker(self, *, client_id: str | None = None) -> Worker:
        """
        Construct an external job worker bound to this client's project.
        Register handlers via ``@worker.handler('task-type')`` and call
        ``await worker.run(stop_event)``.
        """
        return Worker(self._api, self._project_id, client_id=client_id)

    async def __aenter__(self) -> "QuantumBPM":
        await self.authenticate()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        # No explicit teardown for the urllib3-based ApiClient.
        return None
