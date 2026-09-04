# Telemetry

Telemetry is optional and failure-isolated. An exporter outage must not change
an Agent result, tool result, session record, or process exit code.

```bash
pi-py --telemetry-jsonl .pi/traces.jsonl -p "Inspect the project"
```

Prompt and tool payloads are excluded by default. `--telemetry-payloads` opts in
to bounded payload attributes, still subject to key-based and known-secret
redaction.

## Model

```text
TracerProvider
  -> Tracer
  -> Span
  -> SpanProcessor
  -> TelemetryExporter

MeterProvider
  -> Counter / Histogram
  -> TelemetryExporter
```

`AgentSessionInstrumentation` maps product events to:

```text
agent.run
  agent.turn
    model.stream
    agent.tool
```

Run, turn, model, and tool spans carry stable correlation IDs. Assistant usage
adds input, output, cache, total-token, and aggregate-cost attributes. The meter
emits token, cost, and tool-call points independently from spans.

## Exporters

The package includes:

- `NoopTelemetryExporter` for disabled telemetry;
- `MemoryTelemetryExporter` for deterministic tests;
- `JsonlTelemetryExporter` for local inspection and ingestion;
- `HttpJsonTelemetryExporter` for a small collector bridge.

The HTTP envelope is project JSON, not protobuf OTLP. A deployment can translate
it at the collector boundary or provide its own `TelemetryExporter`.

## Redaction

`TelemetryRedactor` applies two controls before export:

1. keys such as authorization, token, password, secret, API key, access token,
   and refresh token are replaced entirely;
2. registered secret values are replaced wherever they occur in strings.

Strings are length-bounded. Complex attribute values are reduced to a safe
string; payload exporters recursively sanitize mappings and sequences.

## Failure behavior

Span processors and exporters are invoked from isolated asynchronous tasks.
Failures are appended to `TracerProvider.diagnostics` or
`MeterProvider.diagnostics`. Shutdown continues through all processors, so one
broken exporter does not prevent others from flushing.

## SDK example

```python
from pi_agent.telemetry import (
    JsonlTelemetryExporter,
    MeterProvider,
    SimpleSpanProcessor,
    TracerProvider,
    instrument_agent_session,
)

exporter = JsonlTelemetryExporter(".pi/traces.jsonl")
traces = TracerProvider((SimpleSpanProcessor(exporter),))
metrics = MeterProvider((exporter,))
instrumentation = instrument_agent_session(session, traces, metrics)

await session.prompt("Run the tests")
await instrumentation.close()
await traces.shutdown()
await metrics.shutdown()
```
