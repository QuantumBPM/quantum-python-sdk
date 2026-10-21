"""Every API call fetches a token from the provider and sends it as the bearer."""

import asyncio
import logging
from unittest.mock import MagicMock

from quantumbpm import QuantumBPM, Vars
from quantumbpm.exceptions import ApiException
from quantumbpm.models.external_job import ExternalJob
from quantumbpm.workers import Job

PROJECT_ID = "00000000-0000-0000-0000-000000000001"


class RotatingProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def get_token(self) -> str:
        self.calls += 1
        return f"token-{self.calls}"


class BrokenProvider:
    async def get_token(self) -> str:
        raise RuntimeError("issuer down")


def make_client(provider) -> tuple[QuantumBPM, list[str]]:
    """The returned list collects the Authorization header each API call saw."""
    client = QuantumBPM(base_url="http://localhost:1", project_id=PROJECT_ID, token_provider=provider)
    seen: list[str] = []

    def record(*_args, **_kwargs):
        seen.append(client.raw.default_headers["Authorization"])
        return MagicMock()

    client.bpmn._bpmn = MagicMock(**{"get_bpmn_resource.side_effect": record})
    client.dmn._api = MagicMock(**{"evaluate_design.side_effect": record})
    client.record = record  # type: ignore[attr-defined]
    return client, seen


def make_raw() -> ExternalJob:
    return ExternalJob(
        id="11111111-1111-1111-1111-111111111111",
        executionKey="wf-1:node-a:1",
        workflowID="wf-1",
        nodeID="node-a",
        taskType="payment",
        status="PENDING",
        createdAt="2026-01-01T00:00:00Z",
    )


def test_bpmn_and_dmn_calls_fetch_a_token_each():
    provider = RotatingProvider()
    client, seen = make_client(provider)

    async def scenario():
        await client.bpmn.get_resource("22222222-2222-2222-2222-222222222222")
        await client.dmn.evaluate_design("<xml/>", Vars())
        await client.bpmn.get_resource("22222222-2222-2222-2222-222222222222")

    asyncio.run(scenario())

    assert provider.calls == 3
    assert seen == ["Bearer token-1", "Bearer token-2", "Bearer token-3"]


def test_worker_calls_fetch_a_token_each():
    provider = RotatingProvider()
    client, seen = make_client(provider)
    w = client.new_worker(client_id="svc")
    w._default = MagicMock()
    w._bpmn = MagicMock()
    w._default.poll_bpmn_external_jobs.side_effect = client.record
    w._default.complete_bpmn_external_job.side_effect = client.record
    w._bpmn.throw_bpmn_external_job_error.side_effect = client.record

    def heartbeat(*args):
        # A lost lock ends the heartbeat loop after exactly one call.
        client.record(*args)
        raise ApiException(status=409, reason="Conflict")

    w._default.heartbeat_bpmn_external_job.side_effect = heartbeat

    async def handler(job: Job):
        return Vars()

    w.handle("payment", handler)
    r = w._registrations["payment"]

    async def scenario():
        await w._poll(r, 1)
        owner = asyncio.create_task(asyncio.Event().wait())
        await w._heartbeat(r, make_raw(), 0.001, owner)
        await w._complete(make_raw(), Vars())
        await w._throw_error(make_raw(), "E", Vars(), retryable=False)

    asyncio.run(scenario())

    assert provider.calls == 4
    assert seen == ["Bearer token-1", "Bearer token-2", "Bearer token-3", "Bearer token-4"]


def test_failing_provider_is_reported_like_a_failed_call(caplog):
    client, _ = make_client(BrokenProvider())
    w = client.new_worker(client_id="svc")
    w._default = MagicMock()
    caplog.set_level(logging.ERROR, logger="quantumbpm.workers")

    asyncio.run(w._complete(make_raw(), Vars()))

    w._default.complete_bpmn_external_job.assert_not_called()
    assert [r.getMessage() for r in caplog.records] == ["complete wf-1:node-a:1: issuer down"]
