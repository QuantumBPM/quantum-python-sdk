"""Optional OpenTelemetry integration for the worker runtime.

OpenTelemetry is an optional dependency (install ``quantumbpm[tracing]`` or add
``opentelemetry-api`` yourself). When it's absent — or present but no SDK is
configured — every helper here is a no-op, so workers that don't opt into
tracing pay nothing.

When enabled, ``job_span`` continues the originating process instance's trace
(from the polled job's ``trace_context``) and opens a worker span that is made
the current span for the handler, so any OpenTelemetry-aware work the handler
does nests beneath it.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

try:
    from opentelemetry import context as _otel_context
    from opentelemetry import propagate, trace
    from opentelemetry.trace import SpanKind, Status, StatusCode

    _tracer = trace.get_tracer("quantumbpm/python-sdk")
    _OTEL = True
except ImportError:  # pragma: no cover - exercised only without the extra
    _OTEL = False


class _NoopSpan:
    def set_attribute(self, *_a: Any, **_k: Any) -> None: ...
    def record_exception(self, *_a: Any, **_k: Any) -> None: ...
    def set_status(self, *_a: Any, **_k: Any) -> None: ...
    def end(self) -> None: ...


@contextmanager
def job_span(raw: Any) -> Iterator[Any]:
    """Open (and make current) a worker span for the duration of the block.

    Yields the span so the caller can annotate outcomes; a no-op span when
    tracing is unavailable.
    """
    if not _OTEL:
        yield _NoopSpan()
        return

    ctx = _otel_context.get_current()
    if raw.trace_context:
        ctx = propagate.extract(raw.trace_context, context=ctx)
    attributes = {
        "bpmn.task_type": raw.task_type,
        "bpmn.node_id": raw.node_id,
        "bpmn.process_instance_id": raw.workflow_id,
        "bpmn.execution_key": raw.execution_key,
    }
    if raw.business_id:
        attributes["bpmn.business_id"] = raw.business_id
    span = _tracer.start_span(
        "bpmn.external-task.execute",
        context=ctx,
        kind=SpanKind.CONSUMER,
        attributes=attributes,
    )
    with trace.use_span(span, end_on_exit=True):
        yield span


def mark_bpmn_error(span: Any, code: str) -> None:
    span.set_attribute("bpmn.error_code", code)


def mark_worker_error(span: Any, exc: BaseException) -> None:
    if not _OTEL or isinstance(span, _NoopSpan):
        return
    span.record_exception(exc)
    span.set_status(Status(StatusCode.ERROR, str(exc)))
