# Telemetry (OpenTelemetry)

wellapi emits native OpenTelemetry traces, metrics and logs. It is designed for
AWS Lambda behind a **collector-only** OpenTelemetry Lambda layer: the layer runs
the OTel Collector as a Lambda extension on `localhost`; wellapi stands up a lean
in-process SDK that exports OTLP/HTTP to it. The heavy SDK + auto-instrumentation
layer is intentionally avoided because of its cold-start cost.

## Mental model

- **Project (infra, via CDK):** attach the collector-only layer, supply its config,
  and set the `OTEL_*` env vars.
- **wellapi (code):** `use_telemetry()` builds the SDK, registers the providers
  globally, owns one root span per invocation, and flushes every signal before the
  Lambda freezes.

## Infrastructure setup (project-side)

Attach the collector-only OpenTelemetry Lambda layer to your function in CDK and
point the collector at your backend. Relevant environment variables:

| Variable | Purpose |
|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Override the exporter target. Defaults to `http://localhost:4318` (the collector). |
| `OTEL_SERVICE_NAME` | Sets `service.name`; overrides the default (the Lambda function name). |
| `OTEL_RESOURCE_ATTRIBUTES` | Extra resource attributes, e.g. `deployment.environment=prod,service.version=1.4.2`. |

A minimal collector config receives OTLP on localhost and exports to your backend
(Tempo / Honeycomb / Datadog / X-Ray via the appropriate exporter).

## Enabling telemetry

```python
from wellapi import WellApi

app = WellApi()
handle = app.use_telemetry()
```

`use_telemetry()` accepts optional `request_hook` / `response_hook` callbacks and
returns a `TelemetryHandle` exposing `.tracer_provider`, `.meter_provider`,
`.logger_provider`.

```python
def request_hook(span, request):
    span.set_attribute("tenant.id", request.headers.get("x-tenant-id", ""))

app.use_telemetry(request_hook=request_hook)
```

Install the extra: `pip install "wellapi[telemetry]"`.

## Adding instrumentors

Because wellapi registers the providers globally and activates the root span in
context, any OTel instrumentor you enable nests under it automatically and exports
through the same collector:

```python
app.use_telemetry()                          # do this first
RequestsInstrumentor().instrument()           # no tracer_provider= -> global
HTTPXClientInstrumentor().instrument()
SQLAlchemyInstrumentor().instrument(engine=engine)
```

Resulting trace tree:

```
SERVER  GET /orders/{id}
├── CLIENT  GET api.partner.com
└── CLIENT  SELECT orders
```

Call `use_telemetry()` before `instrument()`. (Reverse order also works — OTel's
`ProxyTracer` resolves to the global provider at span-creation time.)

## Trace context propagation

Every invocation starts its **own** trace. The inbound W3C trace context — the
`traceparent` header on API Gateway requests, or the `traceparent` SQS message
attribute — is attached to the root span as a **span link**, not used as its
parent. This is deliberate: a sticky or shared upstream `traceparent` (e.g. a
client that reuses one trace across many calls, or a producer that fans out many
messages within a single trace) would otherwise collapse unrelated invocations
into a single `trace_id`. Linking instead of parenting keeps each invocation a
distinct trace while preserving the correlation back to the caller.

## Logs

wellapi configures and flushes a `LoggerProvider` but does not attach handlers —
you choose which loggers to bridge. Because the root span is active in context,
records emitted during handling are correlated automatically (they carry
`trace_id` / `span_id`):

```python
import logging
from opentelemetry.sdk._logs import LoggingHandler

# uses the global provider that wellapi configured
logging.getLogger().addHandler(LoggingHandler(level=logging.INFO))
```

Or pass the provider explicitly via the handle:

```python
handle = app.use_telemetry()
logging.getLogger().addHandler(
    LoggingHandler(level=logging.INFO, logger_provider=handle.logger_provider)
)
```

## Resource attributes & service naming

wellapi builds the OTel `Resource` from AWS Lambda env vars (FaaS semantic
conventions): `cloud.provider`, `cloud.platform`, `cloud.region`, `faas.name`,
`faas.version`, `faas.instance`, `faas.max_memory`, and `service.name` (defaults
to the function name).
`OTEL_SERVICE_NAME` and `OTEL_RESOURCE_ATTRIBUTES` override these without code
changes.

## Local development

Telemetry is off until `use_telemetry()` is called. If you call it locally without
a collector, exports simply fail and are dropped (non-fatal). To see telemetry
locally, run a collector and point `OTEL_EXPORTER_OTLP_ENDPOINT` at it.

## Migration from the previous API

The old `Telemetry(tracer_provider, meter_provider)` class and the
`use_telemetry(telemetry, ...)` signature are removed. Replace:

```python
# before
app.use_telemetry(Telemetry(tracer_provider, meter_provider))
# after
app.use_telemetry()
```
