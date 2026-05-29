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

## SnapStart and ID uniqueness

When `warmup`/`use_snap_start` is on, the function is published with AWS Lambda
SnapStart: Lambda boots one environment, snapshots its memory, and restores that
**same** snapshot into every execution environment. Anything seeded once at init
is therefore shared by all restored environments.

OpenTelemetry's default ID generator draws trace/span IDs from Python's
module-level `random` (a Mersenne Twister seeded once at import). Under SnapStart
that frozen state is cloned, so every restored environment emits the **same
sequence** of trace/span IDs — you see one `trace_id` reused across unrelated
requests. wellapi avoids this by configuring the tracer provider with a
`SystemRandom` (`os.urandom`-backed) ID generator: the kernel CSPRNG is reseeded
with fresh entropy on restore, so IDs stay unique. No action needed for telemetry.

If your own handlers (or other libraries) rely on the `random` module, `uuid1`,
or any non-CSPRNG source for uniqueness, they have the same SnapStart hazard.
Reseed them in an after-restore hook:

```python
import random
from snapshot_restore_py import register_after_restore

@register_after_restore
def _reseed():
    random.seed()  # pull fresh entropy from the OS after restore
```

`os.urandom`, `secrets`, and `uuid4` read the kernel CSPRNG and are already safe.

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
