"""Optional, failure-isolated tracing and metrics for AgentSession."""

from .agent import AgentTelemetry, attach_agent_telemetry
from .exporters import (
    CompositeExporter,
    HttpJsonExporter,
    HttpJsonTelemetryExporter,
    InMemoryExporter,
    JsonlExporter,
    JsonlTelemetryExporter,
    MemoryExporter,
    MemoryTelemetryExporter,
    NoopExporter,
    NoopTelemetryExporter,
    NullExporter,
)
from .instrumentation import AgentSessionInstrumentation, instrument_agent_session
from .metrics import Counter, Histogram, MeterProvider
from .redaction import DEFAULT_REDACTOR, TelemetryRedactor, redact_payload
from .tracer import Meter, Span, Tracer
from .tracing import BatchSpanProcessor, SimpleSpanProcessor, TracerProvider
from .types import (
    AttributeValue,
    MetricKind,
    MetricPoint,
    SpanContext,
    SpanData,
    SpanEvent,
    SpanKind,
    SpanProcessor,
    SpanStatus,
    TelemetryDiagnostic,
    TelemetryError,
    TelemetryEvent,
    TelemetryExporter,
)

__all__ = [
    "DEFAULT_REDACTOR",
    "AgentSessionInstrumentation",
    "AgentTelemetry",
    "AttributeValue",
    "BatchSpanProcessor",
    "CompositeExporter",
    "Counter",
    "Histogram",
    "HttpJsonExporter",
    "HttpJsonTelemetryExporter",
    "InMemoryExporter",
    "JsonlExporter",
    "JsonlTelemetryExporter",
    "MemoryExporter",
    "MemoryTelemetryExporter",
    "Meter",
    "MeterProvider",
    "MetricKind",
    "MetricPoint",
    "NoopExporter",
    "NoopTelemetryExporter",
    "NullExporter",
    "SimpleSpanProcessor",
    "Span",
    "SpanContext",
    "SpanData",
    "SpanEvent",
    "SpanKind",
    "SpanProcessor",
    "SpanStatus",
    "TelemetryDiagnostic",
    "TelemetryError",
    "TelemetryEvent",
    "TelemetryExporter",
    "TelemetryRedactor",
    "Tracer",
    "TracerProvider",
    "attach_agent_telemetry",
    "instrument_agent_session",
    "redact_payload",
]
