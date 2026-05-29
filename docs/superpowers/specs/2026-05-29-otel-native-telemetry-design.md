# Native OpenTelemetry telemetry

## Problem

The current `wellapi.telemetry` module imports `opentelemetry` types but its
emission is shaped for **Logfire**, not a plain OpenTelemetry SDK:

- **Logfire message templates.** The span name is passed as a template string
  with `{}` placeholders (`"{method} {url.path}"`) and relies on Logfire to
  format it from attributes. A native SDK uses the literal string as the span
  name, so spans become `{method} {url.path}`. The template also uses
  `url.path` (the real path with ids) → high-cardinality span names.
- **`_tags`.** `_tags=[tag]` is a Logfire-specific tagging mechanism passed as a
  span attribute. Native OTel has no such concept.
- **Bring-your-own-provider.** `Telemetry(tracer_provider, meter_provider)`
  expects the caller to hand in fully configured providers (today: Logfire's).
  The framework cannot stand up an SDK itself, and the module is undocumented.

There is also a latent bug: the middleware uses `start_span()`, which creates a
**detached** span — it never becomes the current span in context. As a result
`get_current_span()` returns an invalid span during handling, so logs and
downstream instrumentation spans neither correlate nor nest.

## Goal

Rework telemetry around the **native OpenTelemetry SDK**, optimised for AWS
Lambda with a **collector-only** OpenTelemetry Lambda layer. wellapi stands up a
lean in-process SDK that exports OTLP/HTTP to the localhost collector, owns the
root server span, and flushes all signals before the execution environment
freezes — while composing cleanly with any OTel instrumentor the project adds.

## Deployment model (context)

The project (not wellapi) attaches a **collector-only** OpenTelemetry Lambda
layer via CDK: layer ARN, collector config, and `OTEL_*` env vars live
project-side. This layer runs the OTel Collector as a Lambda extension on
`localhost` (OTLP `:4318`); it does **not** ship the Python SDK or
auto-instrument the handler. The heavy SDK + auto-instrumentation layer is
deliberately avoided because of its cold-start cost.

Consequence: with a collector-only layer nothing configures the Python SDK
automatically, so wellapi configures a lean SDK in-process and points its
exporter at the localhost collector. Because the layer does not auto-instrument
the handler, wellapi is the sole owner of the root span — there is no competing
span to disable or nest under.

## Non-goals

- Configuring the collector, managing the Lambda layer, or touching
  `build/cdk.py` for telemetry. All infra is project-side.
- Bundling or choosing instrumentors (`requests`, `httpx`, `sqlalchemy`, …).
  The project decides which to enable; wellapi only provides the global
  providers and the active span they hang off.
- Attaching logging handlers/filters or choosing log levels/format. The project
  attaches `LoggingHandler` to the loggers it wants.
- Supporting the SDK + auto-instrumentation Lambda layer flavor.
- Using OTLP/gRPC (pulls in `grpcio` → cold-start + package-size cost).

## Design

### 1. Architecture & boundaries

wellapi configures a **lean in-process OTel SDK**, lazily, only when
`use_telemetry()` is called:

- Builds a `Resource` from FaaS semantic conventions + `OTEL_RESOURCE_ATTRIBUTES`
  / `OTEL_SERVICE_NAME`.
- Creates `TracerProvider`, `MeterProvider`, `LoggerProvider` with **OTLP/HTTP
  (protobuf)** exporters → `http://localhost:4318` by default (override via
  `OTEL_EXPORTER_OTLP_ENDPOINT`).
- Uses `BatchSpanProcessor` / batching log processor; the middleware
  `force_flush()`es at the end of each invocation.
- **Registers all three providers as global** (`trace.set_tracer_provider`,
  `metrics.set_meter_provider`, `set_logger_provider`).

**Escape hatch (idempotency).** If a real (SDK) global provider already exists
when `use_telemetry()` runs, wellapi reuses it instead of overwriting. This lets
a project pre-configure custom processors/exporters and still get wellapi's span
+ flush behaviour.

Lazy SDK import (only on `use_telemetry()`) plus OTLP/HTTP keeps the cold-start
impact minimal.

### 2. Public API

The `Telemetry(tracer_provider, meter_provider)` class is **removed**.

```python
handle = app.use_telemetry(
    request_hook=None,    # Callable[[Span, Request], None] | None
    response_hook=None,   # Callable[[Span, Response | None], None] | None
)
```

`use_telemetry()`:
1. configures the lean SDK and sets globals (or reuses existing globals),
2. installs `TelemetryMiddleware`,
3. returns a lightweight `TelemetryHandle` exposing `.tracer_provider`,
   `.meter_provider`, `.logger_provider`.

The handle lets the project pass a provider explicitly where that reads better
(e.g. `LoggingHandler(logger_provider=handle.logger_provider)` or
`Instrumentor().instrument(tracer_provider=handle.tracer_provider)`); the global
registration means the no-arg forms work too.

### 3. Invocation lifecycle (`TelemetryMiddleware`)

1. `propagate.extract(...)` — extract inbound W3C `traceparent` from API Gateway
   headers / SQS message attributes (distributed tracing).
2. Build semconv attributes for the event type (see §4).
3. `tracer.start_as_current_span(name, context=parent, kind=SERVER,
   attributes=...)` — **activate the span in context** (fixes the detached-span
   bug; enables log correlation and child-span nesting).
4. `request_hook(span, request)` if set.
5. Call `next_call(request)`. On exception: `span.set_status(ERROR)`,
   `span.record_exception(exc)`, `status_code = 500`, remember the exception.
   Otherwise `status_code = response.statusCode`.
6. Set `http.response.status_code`; call `response_hook(span, response)` if set.
7. Record the duration metric (see §5).
8. `force_flush()` over all configured signals (see §6).
9. Re-raise the remembered exception if any.

### 4. Span naming & attributes

Replace the Logfire message template with a concrete, **low-cardinality** span
name computed in the attribute builder, per semconv:

- HTTP: `{http.request.method} {http.route}` → e.g. `GET /users/{id}`
  (the route template, **not** the real path).
- SQS: `{messaging.destination.name} process` → e.g. `my-queue process`.
- Job: the job name.

`_tags` is **removed**; trigger type is already expressed through `faas.trigger`
/ `messaging.system`. The remaining http/sqs/job/faas/lambda semconv attributes
are kept (they are already correct).

### 5. Metrics

- HTTP: histogram `http.server.request.duration`, unit `s`, attributes
  `http.request.method`, `http.route`, `http.response.status_code`.
- SQS / Job: histogram `faas.invoke_duration`, unit `ms`, with trigger-appropriate
  attributes.

(Replaces today's `http.server.duration` in ms with `http.method` /
`http.target` / `http.status_code` attributes.)

### 6. Flush

A unified `force_flush(timeout_millis=3000)` flushes the **global** tracer,
meter and logger providers. Each is guarded by
`hasattr(provider, "force_flush")`, so a signal that is not configured is simply
skipped. The timeout is a **shared budget across all three** (≈3000 ms total),
so flushing cannot blow past the Lambda timeout or add 3×3000 ms to the response.

Because all instrumentor spans share wellapi's global `BatchSpanProcessor`, this
one flush also drains spans produced by project instrumentors.

### 7. Log correlation (OTel logs only)

Correlation is automatic: with the root span active in context (§3), the OTel
`LogRecord` captures `trace_id` / `span_id` at emit time. wellapi:

- provides the global `LoggerProvider` (and the handle from `use_telemetry()`),
- flushes it (§6).

The project attaches the bridge to the loggers/levels it wants — wellapi does
**not** install handlers or filters:

```python
from opentelemetry.sdk._logs import LoggingHandler
logging.getLogger().addHandler(LoggingHandler(level="INFO"))  # uses global provider
```

### 8. Instrumentor composition

Because providers are global and the root span is active, project instrumentors
nest under the wellapi span and export through wellapi's exporter automatically:

```python
handle = app.use_telemetry()
RequestsInstrumentor().instrument()           # no tracer_provider → global
HTTPXClientInstrumentor().instrument()
SQLAlchemyInstrumentor().instrument(engine=engine)
```

Yields one trace tree (SERVER root + CLIENT/DB children), drained by the §6
flush. Recommended order: `use_telemetry()` before `instrument()`; OTel's
`ProxyTracer` resolves to the global provider at span-creation time, so reverse
order still works.

### 9. Dependencies

The `telemetry` extra becomes `opentelemetry-sdk` +
`opentelemetry-exporter-otlp-proto-http` (no `grpcio`). If `use_telemetry()` is
called without these installed, raise the existing helpful ImportError. If
`use_telemetry()` is never called, telemetry is off and no OTel runtime is
required. Locally, exporting to a non-existent collector is non-fatal (the OTLP
exporter logs and drops).

### 10. File structure (`src/wellapi/telemetry/`)

- `config.py` *(new)* — `configure_telemetry()`: builds Resource, providers,
  exporters; sets globals (or reuses existing); returns `TelemetryHandle`.
- `attributes.py` *(new)* — `RequestAttribute` builders (span name + semconv
  attributes) for http/sqs/job. Extracted from `middleware.py` into a focused,
  unit-testable module.
- `flush.py` *(new)* — `force_flush()` over the three signals with a shared
  budget.
- `middleware.py` — `TelemetryMiddleware`: root span, status, metric, flush.
- `telemetry.py` — **deleted** (the `Telemetry` provider-injection class is gone).
- `__init__.py` — public exports (`TelemetryHandle`, hooks types).

## Tests

wellapi gains a small telemetry test suite (the SDK is a dev dependency):

- **Attribute builders** — deterministic in→out: span name and semconv
  attributes for API Gateway / SQS / Job events; high-cardinality path is not in
  the span name.
- **Middleware (integration)** via `InMemorySpanExporter` + `InMemoryMetricReader`
  (test overrides the exporter): asserts span name, attributes, `kind=SERVER`,
  `ERROR` status + recorded exception on failure, status-code attribute, the
  duration metric, that the span is **active in context** during the call (a log
  / child span emitted inside carries the trace context), and that `force_flush`
  is invoked.
- **Config** — providers are registered global, the handle exposes all three,
  and an existing global provider is reused rather than overwritten.

## Migration & compatibility

Breaking changes: the `Telemetry` class is removed and `use_telemetry()` changes
signature (no provider arguments, returns a handle). Bump the version and update
`README.md` / `docs/framework-usage.md` with the collector-only layer setup,
required `OTEL_*` env vars, and the instrumentor/logging examples above.

## Out of scope / follow-ups

- A `wellapi`-managed CDK construct for the collector layer (stays project-side
  for now).
- Per-trigger metric tuning beyond the §5 histograms.
