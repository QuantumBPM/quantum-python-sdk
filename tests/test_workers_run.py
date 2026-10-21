import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from quantumbpm import workers
from quantumbpm.exceptions import ApiException
from quantumbpm.models.external_job import ExternalJob
from quantumbpm.workers import Job, Vars, Worker


@pytest.fixture(autouse=True)
def fast_heartbeat(monkeypatch):
    monkeypatch.setattr(workers, "MIN_HEARTBEAT_INTERVAL_S", 0.01)


def make_worker(**kwargs) -> Worker:
    w = Worker(MagicMock(), "00000000-0000-0000-0000-000000000001", **kwargs)
    w._default = MagicMock()
    w._bpmn = MagicMock()
    return w


def make_raw(n: int = 1) -> ExternalJob:
    return ExternalJob(
        id=f"11111111-1111-1111-1111-11111111111{n}",
        executionKey=f"wf-1:node-a:{n}",
        workflowID="wf-1",
        nodeID="node-a",
        taskType="payment",
        status="PENDING",
        createdAt="2026-01-01T00:00:00Z",
    )


def serve(w: Worker, *batches: list[ExternalJob]) -> None:
    """Polls hand out ``batches`` in order, then idle like an empty long-poll."""
    pending = list(batches)

    def poll(_project_id, _body):
        if pending:
            return pending.pop(0)
        time.sleep(0.01)
        return []

    w._default.poll_bpmn_external_jobs.side_effect = poll


async def until(cond, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "condition not met in time"
        await asyncio.sleep(0.005)


def test_heartbeat_409_cancels_handler_and_skips_complete(caplog):
    w = make_worker()
    serve(w, [make_raw()])
    heartbeat = w._default.heartbeat_bpmn_external_job
    heartbeat.side_effect = ApiException(status=409, reason="Conflict")

    async def scenario():
        cancelled = asyncio.Event()

        async def handler(job: Job):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        w.handle("payment", handler, lock_duration="10ms")
        stop = asyncio.Event()
        run = asyncio.create_task(w.run(stop))
        await asyncio.wait_for(cancelled.wait(), 2)
        stop.set()
        await asyncio.wait_for(run, 2)

    caplog.set_level(logging.WARNING, logger="quantumbpm.workers")
    asyncio.run(scenario())

    assert heartbeat.call_count == 1
    w._default.complete_bpmn_external_job.assert_not_called()
    w._bpmn.throw_bpmn_external_job_error.assert_not_called()
    lost = [r for r in caplog.records if "no longer holds the job, handler cancelled" in r.getMessage()]
    assert len(lost) == 1
    assert lost[0].levelno == logging.WARNING


def test_heartbeat_follows_the_lock_the_server_granted():
    w = make_worker()
    raw = make_raw()
    # Requested 10s, granted 200ms: heartbeats come every 100ms, not every 5s.
    raw.lock_expires_at = datetime.now(timezone.utc) + timedelta(milliseconds=200)
    serve(w, [raw])
    heartbeat = w._default.heartbeat_bpmn_external_job

    async def scenario():
        release = asyncio.Event()

        async def handler(job: Job):
            await release.wait()
            return Vars()

        w.handle("payment", handler, lock_duration="10s")
        stop = asyncio.Event()
        run = asyncio.create_task(w.run(stop))
        await until(lambda: heartbeat.call_count >= 2)
        stop.set()
        release.set()
        await asyncio.wait_for(run, 2)

    asyncio.run(scenario())

    assert heartbeat.call_args.args[2].lock_duration == "10s"
    w._default.complete_bpmn_external_job.assert_called_once()


def test_stop_lets_running_handler_heartbeat_and_complete():
    w = make_worker()
    serve(w, [make_raw()])
    heartbeat = w._default.heartbeat_bpmn_external_job

    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()

        async def handler(job: Job):
            started.set()
            await release.wait()
            return Vars().set("ok", True)

        w.handle("payment", handler, lock_duration="10ms")
        stop = asyncio.Event()
        run = asyncio.create_task(w.run(stop))
        await asyncio.wait_for(started.wait(), 2)
        stop.set()
        beats_at_stop = heartbeat.call_count
        await until(lambda: heartbeat.call_count >= beats_at_stop + 2)
        assert not run.done()
        release.set()
        await asyncio.wait_for(run, 2)

    asyncio.run(scenario())

    # The only slot was busy, so no poll started after the first one.
    assert w._default.poll_bpmn_external_jobs.call_count == 1
    complete = w._default.complete_bpmn_external_job
    complete.assert_called_once()
    assert complete.call_args.args[1] == "wf-1:node-a:1"


def test_shutdown_timeout_cancels_unfinished_handler():
    w = make_worker(shutdown_timeout=0.1)
    serve(w, [make_raw()])
    heartbeat = w._default.heartbeat_bpmn_external_job

    async def scenario():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def handler(job: Job):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        w.handle("payment", handler, lock_duration="10ms")
        stop = asyncio.Event()
        run = asyncio.create_task(w.run(stop))
        await asyncio.wait_for(started.wait(), 2)
        stop.set()
        await asyncio.wait_for(run, 2)
        assert cancelled.is_set()

        # Heartbeats stop with the handler. One call may already be in flight.
        beats = heartbeat.call_count
        await asyncio.sleep(0.05)
        assert heartbeat.call_count <= beats + 1

    asyncio.run(scenario())

    w._default.complete_bpmn_external_job.assert_not_called()
    w._bpmn.throw_bpmn_external_job_error.assert_not_called()


def test_poll_requests_only_free_slots():
    w = make_worker()
    serve(w, [make_raw(1), make_raw(2)])
    poll = w._default.poll_bpmn_external_jobs

    async def scenario():
        release = asyncio.Event()

        async def handler(job: Job):
            await release.wait()
            return Vars()

        w.handle("payment", handler, max_jobs=3)
        stop = asyncio.Event()
        run = asyncio.create_task(w.run(stop))
        await until(lambda: poll.call_count >= 2)
        stop.set()
        release.set()
        await asyncio.wait_for(run, 2)

    asyncio.run(scenario())

    requested = [c.args[1].max_jobs for c in poll.call_args_list]
    assert requested[0] == 3
    assert requested[1:] and all(n == 1 for n in requested[1:])
    assert w._default.complete_bpmn_external_job.call_count == 2


def test_complete_409_logs_lock_lost(caplog):
    w = make_worker()
    w._default.complete_bpmn_external_job.side_effect = ApiException(
        status=409, reason="Conflict", body='{"code":"CONFLICT","message":"the job was canceled"}'
    )
    caplog.set_level(logging.WARNING, logger="quantumbpm.workers")

    asyncio.run(w._complete(make_raw(), Vars()))

    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (logging.WARNING, "complete wf-1:node-a:1: result discarded: the job was canceled")
    ]
