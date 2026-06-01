import asyncio
from unittest.mock import MagicMock

from quantumbpm.models.external_job import ExternalJob
from quantumbpm.workers import Job, Vars, Worker


def make_worker() -> Worker:
    return Worker(MagicMock(), "00000000-0000-0000-0000-000000000001")


def make_raw(business_id: str | None = None) -> ExternalJob:
    return ExternalJob(
        id="11111111-1111-1111-1111-111111111111",
        executionKey="wf-1:node-a:1",
        workflowID="wf-1",
        nodeID="node-a",
        taskType="payment",
        status="PENDING",
        createdAt="2026-01-01T00:00:00Z",
        businessId=business_id,
    )


def test_job_exposes_business_id_when_present():
    w = make_worker()
    received: list[Job] = []

    async def handler(job: Job):
        received.append(job)
        return Vars()

    w.handle("payment", handler)
    r = w._registrations["payment"]

    raw = make_raw(business_id="ORDER-42")

    # Avoid the real API path for complete/heartbeat — they're not under test.
    async def noop(*_args, **_kwargs):
        return None

    w._complete = noop  # type: ignore[method-assign]
    w._heartbeat = noop  # type: ignore[method-assign]
    w._throw_error = noop  # type: ignore[method-assign]

    asyncio.run(w._dispatch(r, raw, asyncio.Event()))

    assert len(received) == 1
    assert received[0].business_id == "ORDER-42"
    assert received[0].raw.business_id == "ORDER-42"


def test_job_business_id_is_none_when_absent():
    w = make_worker()
    received: list[Job] = []

    async def handler(job: Job):
        received.append(job)
        return Vars()

    w.handle("payment", handler)
    r = w._registrations["payment"]

    raw = make_raw(business_id=None)

    async def noop(*_args, **_kwargs):
        return None

    w._complete = noop  # type: ignore[method-assign]
    w._heartbeat = noop  # type: ignore[method-assign]
    w._throw_error = noop  # type: ignore[method-assign]

    asyncio.run(w._dispatch(r, raw, asyncio.Event()))

    assert len(received) == 1
    assert received[0].business_id is None
