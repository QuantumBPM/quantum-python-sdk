"""
External job worker runtime. Register a handler per task type with
``@worker.handler("task-type")``, then call ``await worker.run(stop_event)``.
The runtime owns long-polling, lock heartbeats, dispatch, and outcome
mapping (Complete on success, ThrowError on a BpmnError, ThrowError with
``WORKER_ERROR`` on any other exception).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import socket
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Generic, TypeVar, cast, get_type_hints
from uuid import UUID

from pydantic import BaseModel, TypeAdapter

from quantumbpm.api.bpmn_api import BpmnApi
from quantumbpm.api.default_api import DefaultApi
from quantumbpm.api_client import ApiClient
from quantumbpm.models.complete_bpmn_external_job_request import CompleteBpmnExternalJobRequest
from quantumbpm.models.external_job import ExternalJob
from quantumbpm.models.heartbeat_bpmn_external_job_request import HeartbeatBpmnExternalJobRequest
from quantumbpm.models.poll_bpmn_job_request import PollBpmnJobRequest
from quantumbpm.tracing import job_span, mark_bpmn_error, mark_worker_error
from quantumbpm.models.throw_bpmn_external_job_error_request import (
    ThrowBpmnExternalJobErrorRequest,
)

from quantumbpm.variables import Vars

log = logging.getLogger("quantumbpm.workers")

DEFAULT_MAX_JOBS = 1
DEFAULT_POLL_TIMEOUT = "30s"
DEFAULT_LOCK_DURATION = "30s"
DEFAULT_MAX_ERROR_MESSAGE_BYTES = 2048
POLL_ERROR_BACKOFF_S = 2.0

TVars = TypeVar("TVars")


class BpmnError(Exception):
    """
    Raise from a handler to fail the job with a BPMN error code. The runtime
    translates it into a ThrowError call against the originating service task
    — matching boundary error events on the task can then route the exception
    in the BPMN model.
    """

    def __init__(self, code: str, variables: Vars | None = None) -> None:
        super().__init__(f"bpmn error: {code}")
        self.code = code
        self.variables = variables


@dataclass
class Job(Generic[TVars]):
    """Job handed to a Handler."""

    execution_key: str
    workflow_id: str
    task_type: str
    vars: Vars
    typed: TVars
    headers: dict[str, str]
    raw: ExternalJob
    business_id: str | None = None
    """Correlation key inherited from the originating BPMN process. ``None``
    when the instance was started without one. Use it for log correlation
    or downstream tracing."""


Handler = Callable[[Job[TVars]], Awaitable[Vars | None]]


@dataclass
class _Registration:
    task_type: str
    handler: Handler[Any]
    typed_model: Any | None
    max_jobs: int
    poll_timeout: str
    lock_duration: str


class Worker:
    """
    Long-poll runtime owning a set of handlers, one per task type. Use
    :meth:`handler` (decorator) or :meth:`handle` (direct registration) to
    register, then call :meth:`run` with an asyncio Event to start polling.
    """

    def __init__(
        self,
        api_client: ApiClient,
        project_id: str | UUID,
        *,
        client_id: str | None = None,
        max_error_message_bytes: int = DEFAULT_MAX_ERROR_MESSAGE_BYTES,
    ) -> None:
        self._default = DefaultApi(api_client)
        self._bpmn = BpmnApi(api_client)
        self._project_id = UUID(project_id) if isinstance(project_id, str) else project_id
        self._client_id = client_id or f"worker-{socket.gethostname()}-{os.getpid()}"
        self._max_error_message_bytes = (
            max_error_message_bytes if max_error_message_bytes > 0 else DEFAULT_MAX_ERROR_MESSAGE_BYTES
        )
        self._registrations: dict[str, _Registration] = {}

    @property
    def client_id(self) -> str:
        return self._client_id

    def handler(
        self,
        task_type: str,
        *,
        max_jobs: int = DEFAULT_MAX_JOBS,
        poll_timeout: str = DEFAULT_POLL_TIMEOUT,
        lock_duration: str = DEFAULT_LOCK_DURATION,
    ) -> Callable[[Handler[Any]], Handler[Any]]:
        """
        Decorator that registers ``fn`` as the handler for ``task_type``.

        If the handler's ``Job`` parameter has a parameterized type
        (e.g. ``Job[EmailJob]``), the runtime decodes the job's variables
        into that type before invoking the handler. The decoded value lands
        in ``job.typed``; ``job.vars`` always carries the raw Vars.
        """

        def decorator(fn: Handler[Any]) -> Handler[Any]:
            self.handle(
                task_type,
                fn,
                max_jobs=max_jobs,
                poll_timeout=poll_timeout,
                lock_duration=lock_duration,
            )
            return fn

        return decorator

    def handle(
        self,
        task_type: str,
        fn: Handler[Any],
        *,
        max_jobs: int = DEFAULT_MAX_JOBS,
        poll_timeout: str = DEFAULT_POLL_TIMEOUT,
        lock_duration: str = DEFAULT_LOCK_DURATION,
    ) -> None:
        """Register ``fn`` as the handler for ``task_type``."""
        self._registrations[task_type] = _Registration(
            task_type=task_type,
            handler=fn,
            typed_model=_extract_typed_model(fn),
            max_jobs=max_jobs,
            poll_timeout=poll_timeout,
            lock_duration=lock_duration,
        )

    async def run(self, stop: asyncio.Event | None = None) -> None:
        """
        Start the polling loops. Resolves when ``stop`` is set, after
        in-flight handlers have settled.
        """
        if not self._registrations:
            raise RuntimeError("workers: no handlers registered")
        stop = stop or asyncio.Event()
        await asyncio.gather(*(self._run_task_type(r, stop) for r in self._registrations.values()))

    async def _run_task_type(self, r: _Registration, stop: asyncio.Event) -> None:
        sem = asyncio.Semaphore(r.max_jobs)
        inflight: set[asyncio.Task[None]] = set()

        while not stop.is_set():
            await sem.acquire()
            try:
                jobs = await self._poll(r)
            except Exception as exc:
                sem.release()
                if stop.is_set():
                    break
                log.error("poll %s: %s", r.task_type, exc)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=POLL_ERROR_BACKOFF_S)
                    break
                except asyncio.TimeoutError:
                    continue

            if not jobs:
                sem.release()
                continue

            # First job uses the slot already acquired; remaining jobs each
            # acquire their own.
            for i, job in enumerate(jobs):
                if i > 0:
                    await sem.acquire()
                task = asyncio.create_task(self._dispatch_with_release(r, job, sem, stop))
                inflight.add(task)
                task.add_done_callback(inflight.discard)

        if inflight:
            await asyncio.gather(*inflight, return_exceptions=True)

    async def _dispatch_with_release(
        self,
        r: _Registration,
        job: ExternalJob,
        sem: asyncio.Semaphore,
        stop: asyncio.Event,
    ) -> None:
        try:
            await self._dispatch(r, job, stop)
        finally:
            sem.release()

    async def _poll(self, r: _Registration) -> list[ExternalJob]:
        body = PollBpmnJobRequest(
            clientID=self._client_id,
            taskType=r.task_type,
            maxJobs=r.max_jobs,
            timeout=r.poll_timeout,
            lockDuration=r.lock_duration,
        )
        result = await asyncio.to_thread(
            self._default.poll_bpmn_external_jobs, self._project_id, body
        )
        return result or []

    async def _dispatch(self, r: _Registration, raw: ExternalJob, stop: asyncio.Event) -> None:
        heartbeat_stop = asyncio.Event()
        hb_task = asyncio.create_task(self._heartbeat(r, raw, heartbeat_stop))

        vars = Vars.from_wire_map(raw.variables)
        typed: Any = vars.to_dict()
        if r.typed_model is not None:
            try:
                typed = _coerce(vars.to_dict(), r.typed_model)
            except Exception as exc:  # decode failure → throw error
                heartbeat_stop.set()
                await hb_task
                log.error("decode vars %s: %s", r.task_type, exc)
                await self._throw_error(
                    raw,
                    "WORKER_ERROR",
                    Vars().set("error", f"decode vars: {exc}"),
                )
                return

        job: Job[Any] = Job(
            execution_key=raw.execution_key,
            workflow_id=raw.workflow_id,
            task_type=r.task_type,
            vars=vars,
            typed=typed,
            headers=raw.headers or {},
            raw=raw,
            business_id=raw.business_id,
        )

        with job_span(raw) as span:
            try:
                result = await r.handler(job)
                heartbeat_stop.set()
                await hb_task
                await self._complete(raw, result if isinstance(result, Vars) else Vars())
            except BpmnError as be:
                heartbeat_stop.set()
                await hb_task
                mark_bpmn_error(span, be.code)
                await self._throw_error(raw, be.code, be.variables or Vars())
            except Exception as exc:
                heartbeat_stop.set()
                await hb_task
                log.exception("handler %s", r.task_type)
                mark_worker_error(span, exc)
                message = self._clamp_worker_error_message(r.task_type, str(exc))
                await self._throw_error(
                    raw,
                    "WORKER_ERROR",
                    Vars().set("error", message),
                )

    async def _heartbeat(
        self, r: _Registration, raw: ExternalJob, stop: asyncio.Event
    ) -> None:
        interval_s = max(1.0, _parse_duration_s(r.lock_duration) / 2)
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_s)
                return
            except asyncio.TimeoutError:
                pass
            try:
                await asyncio.to_thread(
                    self._default.heartbeat_bpmn_external_job,
                    self._project_id,
                    raw.execution_key,
                    HeartbeatBpmnExternalJobRequest(
                        clientID=self._client_id,
                        lockDuration=r.lock_duration,
                    ),
                )
            except Exception as exc:
                log.warning("heartbeat %s: %s", raw.execution_key, exc)

    async def _complete(self, raw: ExternalJob, vars: Vars) -> None:
        try:
            body = CompleteBpmnExternalJobRequest(
                workflowID=raw.workflow_id,
                variables=vars.to_wire_map(),
            )
            await asyncio.to_thread(
                self._default.complete_bpmn_external_job,
                self._project_id,
                raw.execution_key,
                body,
            )
        except Exception as exc:
            log.error("complete %s: %s", raw.execution_key, exc)

    async def _throw_error(self, raw: ExternalJob, code: str, vars: Vars) -> None:
        try:
            body = ThrowBpmnExternalJobErrorRequest(
                errorCode=code,
                variables=vars.to_wire_map(),
            )
            await asyncio.to_thread(
                self._bpmn.throw_bpmn_external_job_error,
                self._project_id,
                raw.execution_key,
                body,
            )
        except Exception as exc:
            log.error("throw_error %s: %s", raw.execution_key, exc)

    def _clamp_worker_error_message(self, task_type: str, msg: str) -> str:
        """
        Shorten an unhandled handler exception's message to the configured
        byte budget. UTF-8 safe (cuts on code-point boundary). Logs a WARN
        and appends a truncation marker when it triggers.
        """
        limit = self._max_error_message_bytes
        encoded = msg.encode("utf-8")
        if limit <= 0 or len(encoded) <= limit:
            return msg
        marker = f"…[truncated, original {len(encoded)} bytes]"
        marker_bytes = len(marker.encode("utf-8"))
        budget = max(0, limit - marker_bytes)
        # `decode(errors="ignore")` drops any half-codepoint at the boundary.
        prefix = encoded[:budget].decode("utf-8", errors="ignore")
        log.warning(
            "workers: WORKER_ERROR message truncated for task=%s from %d to %d bytes",
            task_type,
            len(encoded),
            limit,
        )
        return prefix + marker


def _extract_typed_model(fn: Handler[Any]) -> Any | None:
    """
    Inspect ``fn``'s first parameter annotation. If it's ``Job[T]`` for some
    concrete ``T``, return ``T`` so the runtime can decode payloads. Returns
    ``None`` for plain ``Job`` or no annotation.
    """
    try:
        hints = get_type_hints(fn, include_extras=True)
    except Exception:
        return None
    if not hints:
        return None
    first = next(iter(hints.values()), None)
    if first is None:
        return None
    args = getattr(first, "__args__", ())
    if not args:
        return None
    target = args[0]
    if target is Any or target is dict or target is None:
        return None
    return target


def _coerce(value: dict[str, Any], type_: Any) -> Any:
    if isinstance(type_, type) and issubclass(type_, BaseModel):
        return type_.model_validate(value)
    return TypeAdapter(type_).validate_python(value)


_DURATION_RE = re.compile(r"^(\d+)(ms|s|m|h)$")


def _parse_duration_s(d: str) -> float:
    """Parse a duration string like '30s', '2m', '1h', '500ms' into seconds."""
    m = _DURATION_RE.match(d.strip())
    if not m:
        return 30.0
    value = int(m.group(1))
    unit = m.group(2)
    return {
        "ms": value / 1000.0,
        "s": float(value),
        "m": value * 60.0,
        "h": value * 3600.0,
    }[unit]
