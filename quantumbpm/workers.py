"""
External job worker runtime. Register a handler per task type with
``@worker.handler("task-type")``, then call ``await worker.run(stop_event)``.
The runtime owns long-polling, lock heartbeats, dispatch, and outcome
mapping (Complete on success; ThrowError on a BpmnError as a business error
routed to a boundary immediately, bypassing retries; ThrowError with
``WORKER_ERROR`` on any other exception as a retryable technical failure).
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
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Generic, TypeVar, cast, get_type_hints
from uuid import UUID

from pydantic import BaseModel, TypeAdapter

from quantumbpm.api.bpmn_api import BpmnApi
from quantumbpm.api.default_api import DefaultApi
from quantumbpm.api_client import ApiClient
from quantumbpm.exceptions import ApiException
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
DEFAULT_POLL_TIMEOUT = "5s"
# One poll request waits at most this long on the server, so a stop takes effect within it.
MAX_POLL_TIMEOUT_S = 5.0
DEFAULT_LOCK_DURATION = "30s"
DEFAULT_MAX_ERROR_MESSAGE_BYTES = 2048
DEFAULT_SHUTDOWN_TIMEOUT_S = 20.0
POLL_ERROR_BACKOFF_S = 2.0
MIN_HEARTBEAT_INTERVAL_S = 1.0

TVars = TypeVar("TVars")


class BpmnError(Exception):
    """
    Raise from a handler to fail the job with a BPMN error code. The runtime
    translates it into a ThrowError call against the originating service task
    - matching boundary error events on the task can then route the exception
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
    lock_duration_s: float


class Worker:
    """
    Long-poll runtime owning a set of handlers, one per task type. Use
    :meth:`handler` (decorator) or :meth:`handle` (direct registration) to
    register, then call :meth:`run` with an asyncio Event to start polling.
    ``shutdown_timeout`` (seconds) bounds how long :meth:`run` waits for
    running handlers once the event is set. ``refresh`` is awaited before
    every API call to put a current bearer token on ``api_client``.
    """

    def __init__(
        self,
        api_client: ApiClient,
        project_id: str | UUID,
        *,
        client_id: str | None = None,
        max_error_message_bytes: int = DEFAULT_MAX_ERROR_MESSAGE_BYTES,
        shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_S,
        refresh: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._default = DefaultApi(api_client)
        self._bpmn = BpmnApi(api_client)
        self._refresh = refresh
        self._project_id = UUID(project_id) if isinstance(project_id, str) else project_id
        client_id = (client_id or "").strip()
        self._client_id = client_id or f"worker-{socket.gethostname()}-{os.getpid()}"
        self._max_error_message_bytes = (
            max_error_message_bytes if max_error_message_bytes > 0 else DEFAULT_MAX_ERROR_MESSAGE_BYTES
        )
        self._shutdown_timeout = (
            shutdown_timeout if shutdown_timeout >= 0 else DEFAULT_SHUTDOWN_TIMEOUT_S
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
        """
        Register ``fn`` as the handler for ``task_type``. Raises ValueError
        when ``poll_timeout`` or ``lock_duration`` is not a Go duration.
        """
        # The server rejects a duration with surrounding spaces.
        poll_timeout, lock_duration = poll_timeout.strip(), lock_duration.strip()
        try:
            poll_s = _parse_duration_s(poll_timeout)
            lock_s = _parse_duration_s(lock_duration)
        except ValueError as exc:
            raise ValueError(f"workers: handler {task_type!r}: {exc}") from None
        if poll_s > MAX_POLL_TIMEOUT_S:
            poll_timeout = f"{MAX_POLL_TIMEOUT_S:g}s"
        self._registrations[task_type] = _Registration(
            task_type=task_type,
            handler=fn,
            typed_model=_extract_typed_model(fn),
            max_jobs=max_jobs,
            poll_timeout=poll_timeout,
            lock_duration=lock_duration,
            lock_duration_s=lock_s,
        )

    async def run(self, stop: asyncio.Event | None = None) -> None:
        """
        Start the polling loops. Once ``stop`` is set, no new poll starts and
        running handlers get up to the shutdown timeout to finish and report.
        Handlers still running after it are cancelled and not reported, so
        their locks run out on the server. Then ``run`` returns.
        """
        if not self._registrations:
            raise RuntimeError("workers: no handlers registered")
        stop = stop or asyncio.Event()
        loops = asyncio.gather(
            *(self._run_task_type(r, stop) for r in self._registrations.values())
        )
        stopped: asyncio.Future[Any] = asyncio.create_task(stop.wait())
        try:
            await asyncio.wait([loops, stopped], return_when=asyncio.FIRST_COMPLETED)
            await asyncio.wait_for(loops, timeout=self._shutdown_timeout)
        except asyncio.TimeoutError:
            log.warning(
                "workers: shutdown timeout of %.1fs passed, unfinished jobs cancelled. "
                "Their locks run out on the server.",
                self._shutdown_timeout,
            )
        finally:
            stopped.cancel()
            loops.cancel()

    async def _run_task_type(self, r: _Registration, stop: asyncio.Event) -> None:
        inflight: set[asyncio.Task[None]] = set()
        stopped = asyncio.create_task(stop.wait())

        try:
            while not stop.is_set():
                free = r.max_jobs - len(inflight)
                if free <= 0:
                    # Wakes when a handler finishes or stop is set.
                    await asyncio.wait([stopped, *inflight], return_when=asyncio.FIRST_COMPLETED)
                    continue

                # A poll that is running when stop is set cannot be cancelled.
                # It returns within MAX_POLL_TIMEOUT_S and the jobs it returns
                # still run and report.
                try:
                    jobs = await self._poll(r, free)
                except Exception as exc:
                    if stop.is_set():
                        break
                    log.error("poll %s: %s", r.task_type, exc)
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=POLL_ERROR_BACKOFF_S)
                        break
                    except asyncio.TimeoutError:
                        continue

                for job in jobs:
                    task = asyncio.create_task(self._dispatch(r, job))
                    inflight.add(task)
                    task.add_done_callback(inflight.discard)

            await asyncio.gather(*inflight, return_exceptions=True)
        finally:
            # Handlers remain here only when this loop is cancelled, by the
            # shutdown timeout or by cancelling run.
            stopped.cancel()
            for task in list(inflight):
                task.cancel()
            await asyncio.gather(*inflight, return_exceptions=True)

    async def _call(self, fn: Callable[..., Any], *args: Any) -> Any:
        """Refresh the bearer token, then run the blocking API call in a thread."""
        if self._refresh is not None:
            await self._refresh()
        return await asyncio.to_thread(fn, *args)

    async def _poll(self, r: _Registration, max_jobs: int) -> list[ExternalJob]:
        body = PollBpmnJobRequest(
            clientID=self._client_id,
            taskType=r.task_type,
            maxJobs=max_jobs,
            timeout=r.poll_timeout,
            lockDuration=r.lock_duration,
        )
        result = await self._call(self._default.poll_bpmn_external_jobs, self._project_id, body)
        return result or []

    async def _dispatch(self, r: _Registration, raw: ExternalJob) -> None:
        vars = Vars.from_wire_map(raw.variables)
        typed: Any = vars.to_dict()
        if r.typed_model is not None:
            try:
                typed = _coerce(vars.to_dict(), r.typed_model)
            except Exception as exc:  # decode failure → throw error
                log.error("decode vars %s: %s", r.task_type, exc)
                await self._throw_error(
                    raw,
                    "WORKER_ERROR",
                    Vars().set("error", f"decode vars: {exc}"),
                    retryable=True,
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

        # A lost lock or the shutdown timeout cancels this task. Nothing below
        # catches CancelledError, so a cancelled job is never reported.
        owner = cast("asyncio.Task[Any]", asyncio.current_task())
        interval_s = _heartbeat_interval_s(
            r.lock_duration_s, raw.lock_expires_at, datetime.now(timezone.utc)
        )
        hb_task = asyncio.create_task(self._heartbeat(r, raw, interval_s, owner))
        with job_span(raw) as span:
            try:
                try:
                    result = await r.handler(job)
                finally:
                    hb_task.cancel()
            except BpmnError as be:
                mark_bpmn_error(span, be.code)
                # Business outcome: route to the boundary immediately
                # (retryable=False), bypassing the retry budget.
                await self._throw_error(raw, be.code, be.variables or Vars(), retryable=False)
            except Exception as exc:
                log.exception("handler %s", r.task_type)
                mark_worker_error(span, exc)
                message = self._clamp_worker_error_message(r.task_type, str(exc))
                # Technical failure: retryable, so the server consumes the retry
                # budget before surfacing it.
                await self._throw_error(
                    raw,
                    "WORKER_ERROR",
                    Vars().set("error", message),
                    retryable=True,
                )
            else:
                await self._complete(raw, result if isinstance(result, Vars) else Vars())

    async def _heartbeat(
        self, r: _Registration, raw: ExternalJob, interval_s: float, owner: asyncio.Task[Any]
    ) -> None:
        """
        Renew the job lock every ``interval_s`` until cancelled. When the
        server answers that this worker no longer holds the job, cancel
        ``owner`` (the dispatch task) and stop.
        """
        while True:
            await asyncio.sleep(interval_s)
            try:
                await self._call(
                    self._default.heartbeat_bpmn_external_job,
                    self._project_id,
                    raw.execution_key,
                    HeartbeatBpmnExternalJobRequest(
                        clientID=self._client_id,
                        lockDuration=r.lock_duration,
                    ),
                )
            except Exception as exc:
                status = _http_status(exc)
                if status in (404, 409):
                    log.warning(
                        "heartbeat %s: %s, handler cancelled and result discarded",
                        raw.execution_key,
                        _conflict_reason(exc),
                    )
                    owner.cancel()
                    return
                log.warning("heartbeat %s: %s", raw.execution_key, exc)

    async def _complete(self, raw: ExternalJob, vars: Vars) -> None:
        try:
            body = CompleteBpmnExternalJobRequest(
                workflowID=raw.workflow_id,
                clientID=self._client_id,
                variables=vars.to_wire_map(),
            )
            await self._call(
                self._default.complete_bpmn_external_job,
                self._project_id,
                raw.execution_key,
                body,
            )
        except Exception as exc:
            _log_report_failure("complete", raw, exc)

    async def _throw_error(
        self, raw: ExternalJob, code: str, vars: Vars, retryable: bool
    ) -> None:
        try:
            body = ThrowBpmnExternalJobErrorRequest(
                errorCode=code,
                clientID=self._client_id,
                retryable=retryable,
                variables=vars.to_wire_map(),
            )
            await self._call(
                self._bpmn.throw_bpmn_external_job_error,
                self._project_id,
                raw.execution_key,
                body,
            )
        except Exception as exc:
            _log_report_failure("throw_error", raw, exc)

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


def _http_status(exc: Exception) -> int | None:
    """HTTP status of a failed API call, ``None`` for any other error."""
    return exc.status if isinstance(exc, ApiException) else None


def _conflict_reason(exc: Exception) -> str:
    """
    The message of the server's error body, which says why this worker no
    longer holds the job, for example that its instance was cancelled.
    """
    try:
        message = json.loads(getattr(exc, "body", None) or "{}").get("message")
    except (ValueError, AttributeError):
        message = None
    return message or "this worker no longer holds the job"


def _log_report_failure(op: str, raw: ExternalJob, exc: Exception) -> None:
    """A 409 means this worker no longer holds the job and the server dropped the result."""
    if _http_status(exc) == 409:
        log.warning("%s %s: result discarded: %s", op, raw.execution_key, _conflict_reason(exc))
    else:
        log.error("%s %s: %s", op, raw.execution_key, exc)


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


def _heartbeat_interval_s(
    requested_s: float, lock_expires_at: datetime | None, now: datetime
) -> float:
    """
    Half of the lock the server granted, which can be shorter than the
    requested one. Falls back to the requested lock when ``lock_expires_at``
    is missing or not in the future (client clock skew).
    """
    base = requested_s
    if lock_expires_at is not None:
        granted = (lock_expires_at - now).total_seconds()
        if granted > 0:
            base = min(requested_s, granted)
    return max(MIN_HEARTBEAT_INTERVAL_S, base / 2)


_DURATION_UNITS_S = {
    "ns": 1e-9,
    "us": 1e-6,
    "µs": 1e-6,  # U+00B5 micro sign
    "μs": 1e-6,  # U+03BC Greek mu
    "ms": 1e-3,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
}
_DURATION_PART = "([0-9]+\\.?[0-9]*|\\.[0-9]+)(ns|us|µs|μs|ms|s|m|h)"
_DURATION_PART_RE = re.compile(_DURATION_PART)
_DURATION_RE = re.compile(f"[-+]?(?:{_DURATION_PART})+")


def _parse_duration_s(d: str) -> float:
    """
    Parse a Go duration string such as '30s', '1m30s', '12.5s' or '500ms'
    into seconds, ignoring surrounding spaces. Raises ValueError for anything
    else Go's time.ParseDuration rejects.
    """
    d = d.strip()
    if d in ("0", "+0", "-0"):
        return 0.0
    if not _DURATION_RE.fullmatch(d):
        raise ValueError(f"invalid duration {d!r}, use a Go duration such as 30s, 1m30s or 12.5s")
    seconds = sum(float(n) * _DURATION_UNITS_S[u] for n, u in _DURATION_PART_RE.findall(d))
    return -seconds if d.startswith("-") else seconds
