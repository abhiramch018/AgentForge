"""OpenTelemetry tracing with safe defaults and cross-process propagation.

Telemetry failures never raise into job execution. Exporters are optional;
without configuration spans stay in-process (still queryable in tests).
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import Span, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import (
    TraceContextTextMapPropagator,
)

from app.observability.context import correlation_fields, is_approval_waiting
from app.observability.redaction import redact_mapping

logger = logging.getLogger(__name__)

_propagator = TraceContextTextMapPropagator()
_memory_exporter: InMemorySpanExporter | None = None
_configured = False


def configure_tracing(
    *,
    service_name: str = "agentforge",
    console: bool = False,
    in_memory: bool = True,
) -> InMemorySpanExporter | None:
    """Idempotent tracer setup. Safe if called multiple times."""

    global _configured, _memory_exporter
    if _configured:
        return _memory_exporter
    try:
        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)
        if in_memory:
            _memory_exporter = InMemorySpanExporter()
            provider.add_span_processor(SimpleSpanProcessor(_memory_exporter))
        if console:
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)
        _configured = True
    except Exception:
        logger.exception("otel_configure_failed")
    return _memory_exporter


def get_tracer(name: str = "agentforge") -> trace.Tracer:
    configure_tracing()
    return trace.get_tracer(name)


def current_trace_id() -> str | None:
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if not ctx.is_valid:
        return None
    return format(ctx.trace_id, "032x")


def inject_trace_context() -> dict[str, str]:
    carrier: dict[str, str] = {}
    try:
        _propagator.inject(carrier)
    except Exception:
        logger.exception("trace_inject_failed")
    return carrier


def attach_trace_context(carrier: dict[str, str] | None) -> Any:
    """Return an OTel context token; caller should detach."""

    if not carrier:
        return None
    try:
        ctx = _propagator.extract(carrier)
        return otel_context.attach(ctx)
    except Exception:
        logger.exception("trace_extract_failed")
        return None


def detach_trace_context(token: Any) -> None:
    if token is None:
        return
    try:
        otel_context.detach(token)
    except Exception:
        logger.exception("trace_detach_failed")


@contextmanager
def start_span(
    name: str,
    *,
    attributes: dict[str, Any] | None = None,
    kind: trace.SpanKind = trace.SpanKind.INTERNAL,
) -> Iterator[Span]:
    """Start a span; telemetry setup failures are swallowed."""

    tracer = get_tracer()
    attrs = redact_mapping({**(attributes or {}), **correlation_fields()})
    if is_approval_waiting():
        attrs["approval.waiting"] = True
    flat: dict[str, str | int | float | bool] = {}
    for key, value in attrs.items():
        if isinstance(value, (str, int, float, bool)):
            flat[key] = value
        else:
            flat[key] = str(value)
    with tracer.start_as_current_span(name, kind=kind, attributes=flat) as span:
        try:
            yield span
        except Exception as exc:
            mark_span_error(span, exc)
            raise


def mark_span_error(span: Span, exc: BaseException) -> None:
    try:
        span.record_exception(exc)
        span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
    except Exception:
        logger.exception("span_error_mark_failed")


def reset_tracing_for_tests() -> InMemorySpanExporter:
    """Force a fresh in-memory exporter (tests only).

    OpenTelemetry's ``set_tracer_provider`` is normally once-only; tests clear
    the internal Once latch so each case can install a clean provider.
    """

    global _configured, _memory_exporter
    _configured = False
    _memory_exporter = None
    # Private OTel API — required so suite order cannot leave a stuck provider.
    trace._TRACER_PROVIDER = None  # type: ignore[attr-defined]
    trace._PROXY_TRACER_PROVIDER = None  # type: ignore[attr-defined]
    once = getattr(trace, "_TRACER_PROVIDER_SET_ONCE", None)
    if once is not None and hasattr(once, "_done"):
        once._done = False  # type: ignore[attr-defined]
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "agentforge-test"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _memory_exporter = exporter
    _configured = True
    return exporter
