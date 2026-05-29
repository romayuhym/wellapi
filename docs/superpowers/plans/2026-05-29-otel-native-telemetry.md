# Native OpenTelemetry Telemetry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework `wellapi.telemetry` off Logfire-specific emission onto a lean native OpenTelemetry SDK, optimised for AWS Lambda behind a collector-only OTel layer.

**Architecture:** `use_telemetry()` lazily stands up a lean in-process SDK (OTLP/HTTP → localhost collector), registers the providers globally, and returns a `TelemetryHandle`. `TelemetryMiddleware` owns one root span per invocation (activated in context), records a duration metric, and flushes all signals before the Lambda freezes. Logfire message templates and `_tags` are gone; span names are low-cardinality semconv names; function-level attributes move to the `Resource`.

**Tech Stack:** Python 3.12, OpenTelemetry SDK + OTLP/HTTP exporter, pydantic, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-05-29-otel-native-telemetry-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `src/wellapi/telemetry/attributes.py` *(new)* | Pure functions: `RequestAttribute`, per-trigger semconv attribute + span-name builders, trace-context carrier extraction, code/cold-start attributes. No OpenTelemetry imports. |
| `src/wellapi/telemetry/config.py` *(new)* | `TelemetryHandle` dataclass, `_build_resource`, `_build_providers`, `configure_telemetry` (sets globals or reuses existing). OTel imported lazily inside functions. |
| `src/wellapi/telemetry/flush.py` *(new)* | `force_flush(handle, timeout_millis)` over the three providers with a shared timeout budget. |
| `src/wellapi/telemetry/middleware.py` *(rewrite)* | `TelemetryMiddleware`: root span, status, metric, flush. |
| `src/wellapi/telemetry/telemetry.py` *(delete)* | The old `Telemetry` provider-injection class. |
| `src/wellapi/telemetry/__init__.py` *(modify)* | Export `TelemetryHandle`, `configure_telemetry`. |
| `src/wellapi/applications.py` *(modify)* | New `use_telemetry()` signature; pass `handle=` to middleware; drop `Telemetry` import. |
| `pyproject.toml` *(modify)* | `telemetry` extra gains the OTLP/HTTP exporter; add dev group + pytest config. |
| `tests/conftest.py` + `tests/telemetry/*` *(new)* | Test fixtures and suite. |
| `docs/telemetry.md` *(new)*, `README.md`, `docs/framework-usage.md` *(modify)* | Documentation. |

---

## Task 1: Dependencies and test scaffolding

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/conftest.py`

- [ ] **Step 1: Add the OTLP/HTTP exporter to the `telemetry` extra and a dev group + pytest config**

In `pyproject.toml`, replace the existing `telemetry` extra:

```toml
telemetry = [
    "opentelemetry-sdk>=1.38.0",
]
```

with:

```toml
telemetry = [
    "opentelemetry-sdk>=1.38.0",
    "opentelemetry-exporter-otlp-proto-http>=1.38.0",
]
```

Then append these two new top-level sections at the end of the file:

```toml
[dependency-groups]
dev = [
    "pytest>=8.3.0",
    "opentelemetry-sdk>=1.38.0",
    "opentelemetry-exporter-otlp-proto-http>=1.38.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra"
pythonpath = ["."]
```

(`pythonpath = ["."]` puts the repo root on `sys.path` so `from tests.conftest import ...` resolves `tests` as a namespace package — no `__init__.py` files needed.)

- [ ] **Step 2: Sync the environment**

Run: `uv sync --extra telemetry --group dev`
Expected: resolves and installs `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`, `pytest`.

- [ ] **Step 3: Create shared test fixtures**

Create `tests/conftest.py`:

```python
import pytest

from wellapi.models import RequestAPIGateway, RequestJob, RequestSQS

# A real upstream W3C trace context, reused by propagation assertions.
TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
TRACE_ID = 0x0AF7651916CD43DD8448EB211C80319C
PARENT_SPAN_ID = 0xB7AD6B7169203331


@pytest.fixture
def api_gateway_event():
    return {
        "version": "1.0",
        "resource": "/users/{id}",
        "path": "/users/123",
        "httpMethod": "GET",
        "headers": {"Host": "api.example.com", "traceparent": TRACEPARENT},
        "multiValueHeaders": {
            "Host": ["api.example.com"],
            "traceparent": [TRACEPARENT],
            "X-Custom": ["v"],
        },
        "queryStringParameters": {"q": "x"},
        "multiValueQueryStringParameters": {"q": ["x"]},
        "requestContext": {
            "accountId": "123456789012",
            "apiId": "abc",
            "domainName": "api.example.com",
            "domainPrefix": "api",
            "extendedRequestId": "ext-1",
            "httpMethod": "GET",
            "identity": {"sourceIp": "1.2.3.4"},
            "path": "/users/123",
            "protocol": "HTTP/1.1",
            "requestId": "req-1",
            "requestTime": "29/May/2026:00:00:00 +0000",
            "requestTimeEpoch": 0,
            "resourceId": "res",
            "resourcePath": "/users/{id}",
            "stage": "prod",
        },
        "pathParameters": {"id": "123"},
        "stageVariables": None,
        "body": None,
        "isBase64Encoded": False,
    }


@pytest.fixture
def sqs_event():
    return {
        "Records": [
            {
                "messageId": "m1",
                "receiptHandle": "rh",
                "body": '{"x": 1}',
                "attributes": {},
                "messageAttributes": {
                    "traceparent": {"stringValue": TRACEPARENT, "dataType": "String"}
                },
                "md5OfBody": "abc",
                "eventSource": "aws:sqs",
                "eventSourceARN": "arn:aws:sqs:eu-west-1:123456789012:my-queue",
                "awsRegion": "eu-west-1",
            }
        ]
    }


@pytest.fixture
def job_event():
    return {
        "version": "0",
        "id": "e1",
        "detail-type": "Scheduled Event",
        "source": "aws.events",
        "account": "123456789012",
        "time": "2026-05-29T00:00:00Z",
        "region": "eu-west-1",
        "resources": ["arn:aws:events:eu-west-1:123456789012:rule/nightly"],
        "detail": {},
    }


@pytest.fixture
def api_gateway_request(api_gateway_event):
    return RequestAPIGateway.create_request_from_event(api_gateway_event)


@pytest.fixture
def sqs_request(sqs_event):
    return RequestSQS.create_request_from_event(sqs_event)


@pytest.fixture
def job_request(job_event):
    return RequestJob.create_request_from_event(job_event)
```

- [ ] **Step 4: Verify pytest collects with no tests yet**

Run: `uv run pytest -q`
Expected: `no tests ran` (exit code 5) — confirms config and conftest import cleanly.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock tests/conftest.py
git commit -m "chore(telemetry): add otlp-http exporter dep + pytest scaffolding"
```

---

## Task 2: `attributes.py` — `RequestAttribute` + API Gateway builder

**Files:**
- Create: `src/wellapi/telemetry/attributes.py`
- Test: `tests/telemetry/test_attributes.py`

- [ ] **Step 1: Write the failing test**

Create `tests/telemetry/test_attributes.py`:

```python
from wellapi.telemetry.attributes import RequestAttribute, get_request_attribute


def test_api_gateway_span_name_uses_route_not_path(api_gateway_request):
    attr = get_request_attribute(api_gateway_request)
    assert isinstance(attr, RequestAttribute)
    assert attr.span_name == "GET /users/{id}"  # route template, never /users/123
    assert "123" not in attr.span_name


def test_api_gateway_semconv_attributes(api_gateway_request):
    attr = get_request_attribute(api_gateway_request)
    a = attr.attributes
    assert attr.kind == "SERVER"
    assert attr.trigger == "http"
    assert attr.method == "GET"
    assert attr.route == "/users/{id}"
    assert a["http.request.method"] == "GET"
    assert a["http.route"] == "/users/{id}"
    assert a["url.path"] == "/users/123"
    assert a["faas.trigger"] == "http"
    assert a["network.peer.address"] == "1.2.3.4"


def test_api_gateway_header_attributes_skip_sensitive_and_trace(api_gateway_request):
    a = get_request_attribute(api_gateway_request).attributes
    assert a["http.request.header.x-custom"] == "v"
    assert "http.request.header.host" not in a
    assert "http.request.header.traceparent" not in a
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/telemetry/test_attributes.py -v`
Expected: FAIL — `ModuleNotFoundError: wellapi.telemetry.attributes`.

- [ ] **Step 3: Write minimal implementation**

Create `src/wellapi/telemetry/attributes.py` (only `typing` is imported here; `inspect`/`os`/`sys` are added later in Task 5 when they are first used, to keep this commit lint-clean):

```python
import typing

from pydantic import BaseModel

from wellapi.models import RequestAPIGateway, RequestJob, RequestSQS

HEADERS_TO_SKIP = {
    "token",
    "x-api-key",
    "authorization",
    "accept",
    "accept-encoding",
    "cache-control",
    "cookie",
    "content-length",
    "content-type",
    "host",
    "request-start-time",
    "connection",
    "postman-token",
    "x-forwarded-port",
    "x-forwarded-proto",
    "traceparent",
    "tracestate",
}


class RequestAttribute(BaseModel):
    span_name: str
    kind: str  # "SERVER" | "CONSUMER" | "INTERNAL"
    method: str
    route: str
    trigger: str  # "http" | "sqs" | "job" | "unknown"
    attributes: dict[str, typing.Any]


def _get_api_gateway_attribute(request: RequestAPIGateway) -> RequestAttribute:
    """https://github.com/open-telemetry/semantic-conventions/blob/main/docs/http/http-spans.md"""
    method = request.raw_event.httpMethod
    route = request.raw_event.resource
    path_with_params = request.raw_event.path + (
        "?" + str(request.query_params) if request.query_params else ""
    )
    attributes: dict[str, typing.Any] = {
        "http.request.method": method,
        "http.route": route,
        "url.path": request.raw_event.path,
        "url.full": f"https://{request.raw_event.requestContext.domainName}{path_with_params}",
        "network.peer.address": request.raw_event.requestContext.identity.get(
            "sourceIp", ""
        ),
        "faas.trigger": "http",
    }
    for key, value in request.headers.items():
        if key.lower() in HEADERS_TO_SKIP:
            continue
        attributes[f"http.request.header.{key}"] = value

    return RequestAttribute(
        span_name=f"{method} {route}",
        kind="SERVER",
        method=method,
        route=route,
        trigger="http",
        attributes=attributes,
    )


def get_request_attribute(
    request: RequestAPIGateway | RequestSQS | RequestJob,
) -> RequestAttribute:
    if isinstance(request, RequestAPIGateway):
        return _get_api_gateway_attribute(request)
    return RequestAttribute(
        span_name="unknown",
        kind="INTERNAL",
        method="_OTHER",
        route="unknown",
        trigger="unknown",
        attributes={},
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/telemetry/test_attributes.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/wellapi/telemetry/attributes.py tests/telemetry/test_attributes.py
git commit -m "feat(telemetry): add RequestAttribute and API Gateway semconv builder"
```

---

## Task 3: `attributes.py` — SQS and Job builders

**Files:**
- Modify: `src/wellapi/telemetry/attributes.py`
- Test: `tests/telemetry/test_attributes.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/telemetry/test_attributes.py`:

```python
def test_sqs_attributes(sqs_request):
    attr = get_request_attribute(sqs_request)
    assert attr.span_name == "my-queue process"
    assert attr.kind == "CONSUMER"
    assert attr.trigger == "sqs"
    assert attr.route == "my-queue"
    a = attr.attributes
    assert a["messaging.system"] == "aws_sqs"
    assert a["messaging.destination.name"] == "my-queue"
    assert a["faas.trigger"] == "pubsub"


def test_job_attributes(job_request, monkeypatch):
    monkeypatch.setenv("JOB_NAME", "nightly")
    monkeypatch.setenv("SCHEDULE_EXPRESSION", "rate(1 day)")
    attr = get_request_attribute(job_request)
    assert attr.span_name == "nightly"
    assert attr.kind == "SERVER"
    assert attr.trigger == "job"
    assert attr.route == "nightly"
    a = attr.attributes
    assert a["job.name"] == "nightly"
    assert a["faas.cron"] == "rate(1 day)"
    assert a["faas.trigger"] == "timer"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/telemetry/test_attributes.py -k "sqs or job" -v`
Expected: FAIL — SQS/Job currently fall through to the `unknown` branch.

- [ ] **Step 3: Write minimal implementation**

In `src/wellapi/telemetry/attributes.py`, add the two builders above `get_request_attribute`:

```python
def _get_sqs_attribute(request: RequestSQS) -> RequestAttribute:
    target = request.raw_event.Records[0].eventSourceARN.split(":")[-1]
    return RequestAttribute(
        span_name=f"{target} process",
        kind="CONSUMER",
        method="SQS",
        route=target,
        trigger="sqs",
        attributes={
            "messaging.system": "aws_sqs",
            "messaging.destination.name": target,
            "messaging.operation.name": "process",
            "messaging.operation.type": "process",
            "faas.trigger": "pubsub",
        },
    )


def _get_job_attribute(_request: RequestJob) -> RequestAttribute:
    # JOB_NAME is set by the framework for scheduled jobs; "job" is a defensive
    # fallback. job.name is wellapi-specific (the scheduled job's name, distinct
    # from the Lambda function name carried by the faas.name resource attribute).
    name = os.environ.get("JOB_NAME") or "job"
    return RequestAttribute(
        span_name=name,
        kind="SERVER",
        method="JOB",
        route=name,
        trigger="job",
        attributes={
            "job.name": name,
            "faas.cron": os.environ.get("SCHEDULE_EXPRESSION", ""),
            "faas.trigger": "timer",
        },
    )
```

Then extend the dispatcher to call them:

```python
def get_request_attribute(
    request: RequestAPIGateway | RequestSQS | RequestJob,
) -> RequestAttribute:
    if isinstance(request, RequestAPIGateway):
        return _get_api_gateway_attribute(request)
    if isinstance(request, RequestSQS):
        return _get_sqs_attribute(request)
    if isinstance(request, RequestJob):
        return _get_job_attribute(request)
    return RequestAttribute(
        span_name="unknown",
        kind="INTERNAL",
        method="_OTHER",
        route="unknown",
        trigger="unknown",
        attributes={},
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/telemetry/test_attributes.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/wellapi/telemetry/attributes.py tests/telemetry/test_attributes.py
git commit -m "feat(telemetry): add SQS and Job semconv attribute builders"
```

---

## Task 4: `attributes.py` — trace-context carrier extraction

**Files:**
- Modify: `src/wellapi/telemetry/attributes.py`
- Test: `tests/telemetry/test_attributes.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/telemetry/test_attributes.py`:

```python
from tests.conftest import TRACEPARENT
from wellapi.telemetry.attributes import get_trace_carrier


def test_carrier_from_api_gateway_headers(api_gateway_request):
    carrier = get_trace_carrier(api_gateway_request)
    assert carrier["traceparent"] == TRACEPARENT


def test_carrier_from_sqs_message_attributes(sqs_request):
    carrier = get_trace_carrier(sqs_request)
    assert carrier["traceparent"] == TRACEPARENT


def test_carrier_from_job_is_empty(job_request):
    assert get_trace_carrier(job_request) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/telemetry/test_attributes.py -k carrier -v`
Expected: FAIL — `ImportError: cannot import name 'get_trace_carrier'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/wellapi/telemetry/attributes.py`:

```python
def get_trace_carrier(
    request: RequestAPIGateway | RequestSQS | RequestJob,
) -> dict[str, str]:
    """Build a text-map carrier for `propagate.extract` from the inbound event."""
    if isinstance(request, RequestAPIGateway):
        return {key: value for key, value in request.headers.items()}
    if isinstance(request, RequestSQS):
        records = request.raw_event.Records
        if not records:
            return {}
        carrier: dict[str, str] = {}
        for key, value in (records[0].messageAttributes or {}).items():
            if isinstance(value, dict):
                string_value = value.get("stringValue")
                if string_value is not None:
                    carrier[key] = string_value
        return carrier
    return {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/telemetry/test_attributes.py -k carrier -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/wellapi/telemetry/attributes.py tests/telemetry/test_attributes.py
git commit -m "feat(telemetry): extract trace-context carrier from events"
```

---

## Task 5: `attributes.py` — code and cold-start attributes

**Files:**
- Modify: `src/wellapi/telemetry/attributes.py`
- Test: `tests/telemetry/test_attributes.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/telemetry/test_attributes.py`:

```python
import wellapi.telemetry.attributes as attrs_mod
from wellapi.telemetry.attributes import get_code_attribute, get_invocation_attribute


def test_code_attribute_empty_without_handler(monkeypatch):
    monkeypatch.delenv("_HANDLER", raising=False)
    assert get_code_attribute() == {}


def test_invocation_attribute_coldstart_toggles(monkeypatch):
    monkeypatch.setattr(attrs_mod, "COLD_START", True)
    first = get_invocation_attribute()
    second = get_invocation_attribute()
    assert first == {"faas.coldstart": True}
    assert second == {"faas.coldstart": False}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/telemetry/test_attributes.py -k "code_attribute or coldstart" -v`
Expected: FAIL — `ImportError: cannot import name 'get_code_attribute'`.

- [ ] **Step 3: Write minimal implementation**

First add the imports `inspect`, `os`, `sys` to the top of `src/wellapi/telemetry/attributes.py` (alongside the existing `import typing`), then add these functions:

```python
COLD_START = True


def get_code_attribute() -> dict[str, typing.Any]:
    handler = os.environ.get("_HANDLER")
    if not handler:
        return {}
    try:
        (mod_name, handler_name) = handler.rsplit(".", 1)
        module = sys.modules[mod_name]
        lambda_handler = getattr(module, handler_name)
        _, line_number = inspect.getsourcelines(lambda_handler)
        file_name = "/".join(mod_name.split("."))
        return {
            "code.filepath": f"{file_name}.py",
            "code.function": handler_name,
            "code.lineno": line_number,
        }
    except (ValueError, OSError, TypeError, AttributeError, KeyError):
        return {}


def get_invocation_attribute() -> dict[str, typing.Any]:
    """Per-invocation attributes (cold start). Resource-level FaaS attributes
    live on the Resource, built once in config.py."""
    global COLD_START
    cold = COLD_START
    COLD_START = False
    return {"faas.coldstart": cold}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/telemetry/test_attributes.py -v`
Expected: PASS (10 passed).

- [ ] **Step 5: Commit**

```bash
git add src/wellapi/telemetry/attributes.py tests/telemetry/test_attributes.py
git commit -m "feat(telemetry): add code and cold-start span attributes"
```

---

## Task 6: `flush.py` — unified flush with a shared budget

**Files:**
- Create: `src/wellapi/telemetry/flush.py`
- Test: `tests/telemetry/test_flush.py`

- [ ] **Step 1: Write the failing test**

Create `tests/telemetry/test_flush.py`:

```python
from dataclasses import dataclass

import wellapi.telemetry.flush as flush_mod
from wellapi.telemetry.flush import force_flush


class FlushProvider:
    def __init__(self):
        self.calls = []

    def force_flush(self, timeout_millis):
        self.calls.append(timeout_millis)
        return True


class NoFlushProvider:
    pass


@dataclass
class Handle:
    tracer_provider: object
    meter_provider: object
    logger_provider: object


def test_flushes_all_three_providers():
    tp, mp, lp = FlushProvider(), FlushProvider(), FlushProvider()
    force_flush(Handle(tp, mp, lp), timeout_millis=3000)
    assert len(tp.calls) == len(mp.calls) == len(lp.calls) == 1


def test_skips_providers_without_force_flush():
    tp = FlushProvider()
    force_flush(Handle(tp, NoFlushProvider(), NoFlushProvider()), timeout_millis=3000)
    assert len(tp.calls) == 1  # no AttributeError from the no-flush providers


def test_shared_budget_decreases(monkeypatch):
    # perf_counter() is called once for the deadline, then once per provider.
    ticks = iter([0.0, 0.0, 1.0, 2.0])
    monkeypatch.setattr(flush_mod.time, "perf_counter", lambda: next(ticks))
    tp, mp, lp = FlushProvider(), FlushProvider(), FlushProvider()
    force_flush(Handle(tp, mp, lp), timeout_millis=3000)
    assert tp.calls[0] == 3000
    assert mp.calls[0] == 2000
    assert lp.calls[0] == 1000


def test_flush_never_raises():
    class Boom:
        def force_flush(self, timeout_millis):
            raise RuntimeError("export failed")

    force_flush(Handle(Boom(), NoFlushProvider(), NoFlushProvider()))  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/telemetry/test_flush.py -v`
Expected: FAIL — `ModuleNotFoundError: wellapi.telemetry.flush`.

- [ ] **Step 3: Write minimal implementation**

Create `src/wellapi/telemetry/flush.py`:

```python
import time
import typing

if typing.TYPE_CHECKING:
    from wellapi.telemetry.config import TelemetryHandle


def force_flush(handle: "TelemetryHandle", timeout_millis: int = 3000) -> None:
    """Flush tracer, meter and logger providers before the Lambda freezes.

    The timeout is a budget shared across all three signals, so flushing cannot
    add up to 3x the per-provider timeout to the response. Flushing the handle's
    providers also drains spans from project instrumentors, because those share
    the same (global) providers. Never raises — a failed export must not fail the
    invocation.
    """
    deadline = time.perf_counter() + timeout_millis / 1000
    for provider in (
        handle.tracer_provider,
        handle.meter_provider,
        handle.logger_provider,
    ):
        if not hasattr(provider, "force_flush"):
            continue
        remaining_ms = max(int((deadline - time.perf_counter()) * 1000), 0)
        if remaining_ms <= 0:
            break
        try:
            provider.force_flush(remaining_ms)
        except Exception:
            pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/telemetry/test_flush.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/wellapi/telemetry/flush.py tests/telemetry/test_flush.py
git commit -m "feat(telemetry): unified force_flush with shared timeout budget"
```

---

## Task 7: `config.py` — `TelemetryHandle` and `_build_resource`

**Files:**
- Create: `src/wellapi/telemetry/config.py`
- Test: `tests/telemetry/test_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/telemetry/test_config.py`:

```python
from wellapi.telemetry.config import TelemetryHandle, _build_resource


def _attrs(resource):
    return dict(resource.attributes)


def test_resource_uses_faas_env(monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "orders-fn")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    monkeypatch.delenv("OTEL_RESOURCE_ATTRIBUTES", raising=False)
    a = _attrs(_build_resource())
    assert a["faas.name"] == "orders-fn"
    assert a["cloud.region"] == "eu-west-1"
    assert a["cloud.platform"] == "aws_lambda"
    assert a["service.name"] == "orders-fn"  # defaults to faas.name


def test_otel_service_name_overrides_default(monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "orders-fn")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "orders-api")
    a = _attrs(_build_resource())
    assert a["service.name"] == "orders-api"  # env wins
    assert a["faas.name"] == "orders-fn"


def test_max_memory_converted_to_bytes(monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", "128")
    a = _attrs(_build_resource())
    assert a["faas.max_memory"] == 128 * 1024 * 1024


def test_handle_is_a_dataclass_of_three_providers():
    handle = TelemetryHandle("tp", "mp", "lp")
    assert (handle.tracer_provider, handle.meter_provider, handle.logger_provider) == (
        "tp",
        "mp",
        "lp",
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/telemetry/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: wellapi.telemetry.config`.

- [ ] **Step 3: Write minimal implementation**

Create `src/wellapi/telemetry/config.py`:

```python
import os
from dataclasses import dataclass
from typing import Any

_INSTALL_HINT = (
    "WellApi telemetry requires `opentelemetry-sdk` and "
    "`opentelemetry-exporter-otlp-proto-http`.\n"
    "Install them with:\n"
    "    uv add 'wellapi[telemetry]'"
)


@dataclass
class TelemetryHandle:
    """Handles to the configured providers. Returned by `app.use_telemetry()` so
    callers can pass a provider explicitly (e.g. to `LoggingHandler` or an
    instrumentor); the providers are also registered globally."""

    tracer_provider: Any
    meter_provider: Any
    logger_provider: Any


def _build_resource() -> Any:
    """Resource from FaaS semconv + OTEL_SERVICE_NAME / OTEL_RESOURCE_ATTRIBUTES.

    Framework FaaS defaults are the base; the OTEL_* env vars override them
    (Resource.create merges the env detector, and we omit service.name from the
    base whenever OTEL_SERVICE_NAME is set so the env value survives)."""
    try:
        from opentelemetry.sdk.resources import Resource
    except ImportError as err:  # pragma: no cover
        raise RuntimeError(_INSTALL_HINT) from err

    name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "")
    attributes: dict[str, Any] = {
        "cloud.provider": "aws",
        "cloud.platform": "aws_lambda",
        "cloud.region": os.environ.get("AWS_REGION", ""),
        "faas.name": name,
        "faas.version": os.environ.get("AWS_LAMBDA_FUNCTION_VERSION", ""),
        "faas.instance": os.environ.get("AWS_LAMBDA_LOG_STREAM_NAME", ""),
    }
    memory_mb = os.environ.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE")
    if memory_mb:
        attributes["faas.max_memory"] = int(memory_mb) * 1024 * 1024
    if name and "OTEL_SERVICE_NAME" not in os.environ:
        attributes["service.name"] = name

    return Resource.create(attributes)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/telemetry/test_config.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/wellapi/telemetry/config.py tests/telemetry/test_config.py
git commit -m "feat(telemetry): TelemetryHandle and FaaS resource builder"
```

---

## Task 8: `config.py` — `_build_providers` and `configure_telemetry`

**Files:**
- Modify: `src/wellapi/telemetry/config.py`
- Test: `tests/telemetry/test_config.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/telemetry/test_config.py`:

```python
import wellapi.telemetry.config as config_mod


def test_build_providers_attaches_resource_and_returns_sdk_types():
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    resource = Resource.create({"service.name": "t"})
    tp, mp, lp = config_mod._build_providers(resource)
    assert isinstance(tp, TracerProvider)
    assert isinstance(mp, MeterProvider)
    assert isinstance(lp, LoggerProvider)
    assert dict(tp.resource.attributes)["service.name"] == "t"


def test_configure_reuses_existing_sdk_provider(monkeypatch):
    from opentelemetry.sdk.trace import TracerProvider

    existing = TracerProvider()
    monkeypatch.setattr(config_mod.trace, "get_tracer_provider", lambda: existing)
    set_called = []
    monkeypatch.setattr(
        config_mod.trace, "set_tracer_provider", lambda p: set_called.append(p)
    )
    handle = config_mod.configure_telemetry()
    assert handle.tracer_provider is existing
    assert set_called == []  # reused, not overwritten


def test_configure_sets_globals_when_unconfigured(monkeypatch):
    from opentelemetry.trace import ProxyTracerProvider

    monkeypatch.setattr(
        config_mod.trace, "get_tracer_provider", lambda: ProxyTracerProvider()
    )
    sentinels = ("TP", "MP", "LP")
    monkeypatch.setattr(config_mod, "_build_resource", lambda: "RES")
    monkeypatch.setattr(config_mod, "_build_providers", lambda resource: sentinels)
    calls = {}
    monkeypatch.setattr(
        config_mod.trace, "set_tracer_provider", lambda p: calls.__setitem__("tp", p)
    )
    monkeypatch.setattr(
        config_mod.metrics, "set_meter_provider", lambda p: calls.__setitem__("mp", p)
    )
    monkeypatch.setattr(
        config_mod, "set_logger_provider", lambda p: calls.__setitem__("lp", p)
    )
    handle = config_mod.configure_telemetry()
    assert (calls["tp"], calls["mp"], calls["lp"]) == sentinels
    assert (handle.tracer_provider, handle.meter_provider, handle.logger_provider) == sentinels
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/telemetry/test_config.py -k "build_providers or configure" -v`
Expected: FAIL — `_build_providers` / `configure_telemetry` / module-level `trace`, `metrics`, `set_logger_provider` not defined.

- [ ] **Step 3: Write minimal implementation**

In `src/wellapi/telemetry/config.py`, add these module-level imports just below the stdlib imports (they make the monkeypatch targets `config_mod.trace` etc. real):

```python
from opentelemetry import metrics, trace
from opentelemetry._logs import get_logger_provider, set_logger_provider
```

Then append:

```python
def _build_providers(resource: Any) -> tuple[Any, Any, Any]:
    try:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as err:
        raise RuntimeError(_INSTALL_HINT) from err

    # Exporters default to OTEL_EXPORTER_OTLP_ENDPOINT or http://localhost:4318,
    # i.e. the collector-only Lambda layer running on localhost.
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())],
    )

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter())
    )
    return tracer_provider, meter_provider, logger_provider


def configure_telemetry() -> TelemetryHandle:
    """Stand up a lean in-process SDK and register it globally, or reuse an
    already-configured global SDK TracerProvider (escape hatch for projects that
    pre-configure custom processors)."""
    try:
        from opentelemetry.sdk.trace import TracerProvider
    except ImportError as err:
        raise RuntimeError(_INSTALL_HINT) from err

    existing = trace.get_tracer_provider()
    if isinstance(existing, TracerProvider):
        return TelemetryHandle(existing, metrics.get_meter_provider(), get_logger_provider())

    resource = _build_resource()
    tracer_provider, meter_provider, logger_provider = _build_providers(resource)
    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(meter_provider)
    set_logger_provider(logger_provider)
    return TelemetryHandle(tracer_provider, meter_provider, logger_provider)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/telemetry/test_config.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/wellapi/telemetry/config.py tests/telemetry/test_config.py
git commit -m "feat(telemetry): configure lean in-process SDK with global registration"
```

---

## Task 9: `middleware.py` — rewrite `TelemetryMiddleware`

**Files:**
- Modify (rewrite): `src/wellapi/telemetry/middleware.py`
- Test: `tests/telemetry/test_middleware.py`

- [ ] **Step 1: Write the failing test**

Create `tests/telemetry/test_middleware.py`:

```python
import pytest
from opentelemetry import trace
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind
from opentelemetry.trace.status import StatusCode

import wellapi.telemetry.middleware as mw_mod
from tests.conftest import PARENT_SPAN_ID, TRACE_ID
from wellapi.models import ResponseAPIGateway
from wellapi.telemetry.config import TelemetryHandle
from wellapi.telemetry.middleware import TelemetryMiddleware


@pytest.fixture
def telemetry():
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader])
    handle = TelemetryHandle(tracer_provider, meter_provider, LoggerProvider())
    return handle, exporter, metric_reader


def _metric_names(metric_reader):
    data = metric_reader.get_metrics_data()
    names = []
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            names.extend(m.name for m in sm.metrics)
    return names


def test_span_name_kind_and_attributes(telemetry, api_gateway_request, monkeypatch):
    handle, exporter, _ = telemetry
    monkeypatch.setattr(mw_mod, "force_flush", lambda *a, **k: None)
    mw = TelemetryMiddleware(lambda req: ResponseAPIGateway(status_code=201), handle)
    mw(api_gateway_request)
    (span,) = exporter.get_finished_spans()
    assert span.name == "GET /users/{id}"
    assert span.kind == SpanKind.SERVER
    assert span.attributes["http.route"] == "/users/{id}"
    assert span.attributes["http.response.status_code"] == 201


def test_span_is_active_in_context(telemetry, api_gateway_request, monkeypatch):
    handle, exporter, _ = telemetry
    monkeypatch.setattr(mw_mod, "force_flush", lambda *a, **k: None)
    captured = {}

    def handler(req):
        captured["ctx"] = trace.get_current_span().get_span_context()
        return ResponseAPIGateway(status_code=200)

    TelemetryMiddleware(handler, handle)(api_gateway_request)
    (span,) = exporter.get_finished_spans()
    assert captured["ctx"].is_valid
    assert captured["ctx"].span_id == span.context.span_id


def test_parent_context_extracted_from_traceparent(
    telemetry, api_gateway_request, monkeypatch
):
    handle, exporter, _ = telemetry
    monkeypatch.setattr(mw_mod, "force_flush", lambda *a, **k: None)
    TelemetryMiddleware(lambda req: ResponseAPIGateway(status_code=200), handle)(
        api_gateway_request
    )
    (span,) = exporter.get_finished_spans()
    assert span.context.trace_id == TRACE_ID
    assert span.parent.span_id == PARENT_SPAN_ID


def test_exception_sets_error_status_and_reraises(
    telemetry, api_gateway_request, monkeypatch
):
    handle, exporter, _ = telemetry
    monkeypatch.setattr(mw_mod, "force_flush", lambda *a, **k: None)

    def boom(req):
        raise ValueError("kaboom")

    with pytest.raises(ValueError):
        TelemetryMiddleware(boom, handle)(api_gateway_request)
    (span,) = exporter.get_finished_spans()
    assert span.status.status_code == StatusCode.ERROR
    assert span.attributes["http.response.status_code"] == 500
    assert any(e.name == "exception" for e in span.events)


def test_http_metric_recorded(telemetry, api_gateway_request, monkeypatch):
    handle, _, metric_reader = telemetry
    monkeypatch.setattr(mw_mod, "force_flush", lambda *a, **k: None)
    TelemetryMiddleware(lambda req: ResponseAPIGateway(status_code=200), handle)(
        api_gateway_request
    )
    assert "http.server.request.duration" in _metric_names(metric_reader)


def test_sqs_metric_recorded(telemetry, sqs_request, monkeypatch):
    handle, _, metric_reader = telemetry
    monkeypatch.setattr(mw_mod, "force_flush", lambda *a, **k: None)
    TelemetryMiddleware(lambda req: ResponseAPIGateway(status_code=200), handle)(
        sqs_request
    )
    assert "faas.invoke_duration" in _metric_names(metric_reader)


def test_force_flush_called_with_handle(telemetry, api_gateway_request, monkeypatch):
    handle, _, _ = telemetry
    flushed = []
    monkeypatch.setattr(mw_mod, "force_flush", lambda h, *a, **k: flushed.append(h))
    TelemetryMiddleware(lambda req: ResponseAPIGateway(status_code=200), handle)(
        api_gateway_request
    )
    assert flushed == [handle]


def test_hooks_invoked(telemetry, api_gateway_request, monkeypatch):
    handle, _, _ = telemetry
    monkeypatch.setattr(mw_mod, "force_flush", lambda *a, **k: None)
    seen = {}
    mw = TelemetryMiddleware(
        lambda req: ResponseAPIGateway(status_code=200),
        handle,
        request_hook=lambda span, req: seen.__setitem__("req", True),
        response_hook=lambda span, resp: seen.__setitem__("resp", resp.statusCode),
    )
    mw(api_gateway_request)
    assert seen == {"req": True, "resp": 200}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/telemetry/test_middleware.py -v`
Expected: FAIL — current `TelemetryMiddleware.__init__` takes `telemetry=`, not `handle`, and uses the removed `Telemetry` API.

- [ ] **Step 3: Write minimal implementation**

Replace the entire contents of `src/wellapi/telemetry/middleware.py` with:

```python
import functools
import time
import typing

try:
    from opentelemetry import propagate
    from opentelemetry.trace import Span, SpanKind
    from opentelemetry.trace.status import Status, StatusCode
except ImportError as err:  # pragma: no cover
    raise RuntimeError(
        "WellApi telemetry requires the `opentelemetry-sdk` package.\n"
        "You can install this with:\n"
        "    uv add 'wellapi[telemetry]'"
    ) from err

from wellapi.models import RequestAPIGateway, RequestJob, RequestSQS, ResponseAPIGateway
from wellapi.telemetry.attributes import (
    get_code_attribute,
    get_invocation_attribute,
    get_request_attribute,
    get_trace_carrier,
)
from wellapi.telemetry.flush import force_flush

if typing.TYPE_CHECKING:
    from wellapi.telemetry.config import TelemetryHandle

_SPAN_KIND = {
    "SERVER": SpanKind.SERVER,
    "CONSUMER": SpanKind.CONSUMER,
    "INTERNAL": SpanKind.INTERNAL,
}


class TelemetryMiddleware:
    def __init__(
        self,
        next_call: typing.Callable,
        handle: "TelemetryHandle",
        request_hook: typing.Callable[[Span, typing.Any], None] | None = None,
        response_hook: typing.Callable[[Span, ResponseAPIGateway | None], None]
        | None = None,
    ) -> None:
        self.next_call = next_call
        self.handle = handle
        self.request_hook = request_hook
        self.response_hook = response_hook
        functools.update_wrapper(self, next_call, updated=())

        meter = handle.meter_provider.get_meter(__name__)
        self.tracer = handle.tracer_provider.get_tracer(__name__)
        self.http_duration = meter.create_histogram(
            name="http.server.request.duration",
            unit="s",
            description="Duration of inbound HTTP requests.",
        )
        self.faas_duration = meter.create_histogram(
            name="faas.invoke_duration",
            unit="ms",
            description="Duration of FaaS invocations (SQS/Job).",
        )

    def __call__(
        self, request: RequestAPIGateway | RequestJob | RequestSQS
    ) -> ResponseAPIGateway:
        start = time.perf_counter()

        attribute = get_request_attribute(request)
        parent = propagate.extract(get_trace_carrier(request))
        span_attributes = {
            **attribute.attributes,
            **get_code_attribute(),
            **get_invocation_attribute(),
        }

        exception: Exception | None = None
        response: ResponseAPIGateway | None = None
        with self.tracer.start_as_current_span(
            attribute.span_name,
            context=parent,
            kind=_SPAN_KIND.get(attribute.kind, SpanKind.SERVER),
            attributes=span_attributes,
        ) as span:
            if self.request_hook:
                self.request_hook(span, request)

            try:
                response = self.next_call(request)
            except Exception as exc:
                span.set_status(Status(StatusCode.ERROR))
                span.record_exception(exc)
                status_code = 500
                exception = exc
            else:
                status_code = response.statusCode
            finally:
                span.set_attribute("http.response.status_code", status_code)

            if self.response_hook:
                self.response_hook(span, response)

        self._record_metric(attribute, status_code, time.perf_counter() - start)
        force_flush(self.handle)

        if exception:
            raise exception.with_traceback(exception.__traceback__)

        return response

    def _record_metric(self, attribute, status_code: int, duration_s: float) -> None:
        if attribute.trigger == "http":
            self.http_duration.record(
                duration_s,
                {
                    "http.request.method": attribute.method,
                    "http.route": attribute.route,
                    "http.response.status_code": status_code,
                },
            )
        else:
            self.faas_duration.record(
                max(round(duration_s * 1000), 0),
                {
                    "faas.trigger": attribute.attributes.get(
                        "faas.trigger", attribute.trigger
                    ),
                    "faas.invoked_name": attribute.route,
                    "faas.status_code": status_code,
                },
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/telemetry/test_middleware.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add src/wellapi/telemetry/middleware.py tests/telemetry/test_middleware.py
git commit -m "feat(telemetry): rewrite middleware for native OTel (root span, flush, metrics)"
```

---

## Task 10: Wire `applications.py`, delete `telemetry.py`, update `__init__.py`

**Files:**
- Modify: `src/wellapi/applications.py` (lines 37, 39-40, 273-285, 323-331)
- Delete: `src/wellapi/telemetry/telemetry.py`
- Modify: `src/wellapi/telemetry/__init__.py`
- Test: `tests/telemetry/test_applications.py`

- [ ] **Step 1: Write the failing test**

Create `tests/telemetry/test_applications.py`:

```python
from opentelemetry.sdk.trace import TracerProvider

from wellapi.applications import WellApi
from wellapi.telemetry.config import TelemetryHandle


def test_use_telemetry_returns_handle_and_enables(monkeypatch):
    import wellapi.applications as app_mod
    import wellapi.telemetry.config as config_mod

    fake = TelemetryHandle(TracerProvider(), object(), object())
    monkeypatch.setattr(config_mod, "configure_telemetry", lambda: fake)
    monkeypatch.setattr(app_mod, "configure_telemetry", lambda: fake, raising=False)

    app = WellApi()
    handle = app.use_telemetry()
    assert handle is fake
    assert app.telemetry is fake


def test_init_exports_public_symbols():
    from wellapi.telemetry import TelemetryHandle as ExportedHandle
    from wellapi.telemetry import configure_telemetry

    assert ExportedHandle is TelemetryHandle
    assert callable(configure_telemetry)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/telemetry/test_applications.py -v`
Expected: FAIL — `use_telemetry` currently requires a `telemetry` argument; `wellapi.telemetry` exports nothing.

- [ ] **Step 3: Update `applications.py` imports**

In `src/wellapi/applications.py`, replace line 37:

```python
from wellapi.telemetry.telemetry import Telemetry
```

with nothing (delete it), and replace the `TYPE_CHECKING` block at lines 39-40:

```python
if TYPE_CHECKING:
    from opentelemetry.trace import Span
```

with:

```python
if TYPE_CHECKING:
    from opentelemetry.trace import Span

    from wellapi.telemetry.config import TelemetryHandle
```

- [ ] **Step 4: Update the middleware wiring**

In `build_middleware_stack`, change the keyword passed to `TelemetryMiddleware` from `telemetry=self.telemetry` to `handle=self.telemetry`:

```python
        if self.telemetry is not None:
            # Import lazily to avoid requiring telemetry extras unless used.
            from wellapi.telemetry.middleware import TelemetryMiddleware

            middleware.insert(
                1,
                Middleware(
                    TelemetryMiddleware,
                    handle=self.telemetry,
                    request_hook=self.request_hook,
                    response_hook=self.response_hook,
                ),
            )
```

- [ ] **Step 5: Rewrite `use_telemetry`**

Replace the existing `use_telemetry` method (lines ~323-331):

```python
    def use_telemetry(
        self,
        request_hook: Callable[["Span", RequestAPIGateway], None] | None = None,
        response_hook: Callable[["Span", ResponseAPIGateway | None], None] | None = None,
    ) -> "TelemetryHandle":
        """Enable native OpenTelemetry. Stands up a lean in-process SDK (OTLP/HTTP
        to the localhost collector), registers the providers globally, installs the
        telemetry middleware, and returns the provider handle. Call this before
        enabling instrumentors (e.g. RequestsInstrumentor().instrument())."""
        from wellapi.telemetry.config import configure_telemetry

        self.telemetry = configure_telemetry()
        self.request_hook = request_hook
        self.response_hook = response_hook
        return self.telemetry
```

- [ ] **Step 6: Delete the old `Telemetry` class**

Run: `git rm src/wellapi/telemetry/telemetry.py`

- [ ] **Step 7: Update package exports**

Replace the contents of `src/wellapi/telemetry/__init__.py` with:

```python
from wellapi.telemetry.config import TelemetryHandle, configure_telemetry

__all__ = ["TelemetryHandle", "configure_telemetry"]
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/telemetry/test_applications.py -v`
Expected: PASS (2 passed).

- [ ] **Step 9: Verify nothing else references the deleted class**

Run: `grep -rn "telemetry.telemetry\|import Telemetry\b\|Telemetry(" src/ tests/`
Expected: no matches.

- [ ] **Step 10: Commit**

```bash
git add src/wellapi/applications.py src/wellapi/telemetry/__init__.py tests/telemetry/test_applications.py
git commit -m "feat(telemetry): new use_telemetry() API; remove Telemetry provider-injection class"
```

---

## Task 11: Documentation

**Files:**
- Create: `docs/telemetry.md`
- Modify: `README.md` (line 37 area — extras list)
- Modify: `docs/framework-usage.md` (telemetry mention around line 26-29)

- [ ] **Step 1: Write the telemetry guide**

Create `docs/telemetry.md`:

````markdown
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
RequestsInstrumentor().instrument()           # no tracer_provider= → global
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
logging.getLogger().addHandler(LoggingHandler(level="INFO"))
```

Or pass the provider explicitly via the handle:

```python
handle = app.use_telemetry()
logging.getLogger().addHandler(
    LoggingHandler(level="INFO", logger_provider=handle.logger_provider)
)
```

## Resource attributes & service naming

wellapi builds the OTel `Resource` from AWS Lambda env vars (FaaS semantic
conventions): `faas.name`, `faas.version`, `faas.instance`, `cloud.region`,
`faas.max_memory`, and `service.name` (defaults to the function name).
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
````

- [ ] **Step 2: Update README extras description**

In `README.md`, replace the line:

```markdown
- `telemetry`: OpenTelemetry support
```

with:

```markdown
- `telemetry`: native OpenTelemetry support (traces, metrics, logs) — see [docs/telemetry.md](docs/telemetry.md)
```

- [ ] **Step 3: Add a telemetry subsection to framework-usage**

In `docs/framework-usage.md`, after the install block (around line 29), add:

```markdown
## Telemetry

wellapi emits native OpenTelemetry signals, designed for AWS Lambda behind a
collector-only OTel layer. Enable it with `app.use_telemetry()`. See
[telemetry.md](telemetry.md) for the full guide (infra setup, instrumentors, logs,
resource attributes).
```

- [ ] **Step 4: Commit**

```bash
git add docs/telemetry.md README.md docs/framework-usage.md
git commit -m "docs(telemetry): add usage guide for the native OpenTelemetry approach"
```

---

## Task 12: Full verification and version bump

**Files:**
- Modify: `pyproject.toml` (version)

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest -v`
Expected: all tests pass (Tasks 2-10 suites).

- [ ] **Step 2: Lint**

Run: `uvx ruff check src/wellapi/telemetry tests`
Expected: no errors. If import-order (`I`) issues are reported, run `uvx ruff check --fix --select I src/wellapi/telemetry tests` and re-run.

- [ ] **Step 3: Confirm telemetry stays optional**

Run: `uv run python -c "import wellapi; from wellapi.applications import WellApi; WellApi()"`
Expected: no error (importing wellapi and constructing the app must not import the OTel SDK).

- [ ] **Step 4: Bump the version**

In `pyproject.toml`, change `version = "0.9.16"` to `version = "0.10.0"` (minor bump — this is a breaking telemetry API change).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "chore: bump version to 0.10.0"
```

---

## Self-Review Notes

- **Spec coverage:** §1 architecture → Tasks 7-8, 10; §2 API → Task 10; §3 lifecycle → Task 9; §4 span naming/attrs + Resource split → Tasks 2-5, 7; §5 metrics → Task 9; §6 flush → Task 6; §7 logs → Tasks 8-9 (provider + active context) + Task 11 docs; §8 instrumentors → Tasks 8 (global) + 11 docs; §9 deps → Task 1; §10 file structure → all; §11 tests → Tasks 2-10; Documentation section → Task 11; Migration → Tasks 10, 12.
- **Type consistency:** `TelemetryHandle(tracer_provider, meter_provider, logger_provider)`, `configure_telemetry()`, `force_flush(handle, timeout_millis)`, `get_request_attribute() -> RequestAttribute(span_name, kind, method, route, trigger, attributes)`, `get_trace_carrier()`, `TelemetryMiddleware(next_call, handle, request_hook, response_hook)` are used consistently across tasks.
- **Note on flush vs spec §6:** the spec says "flush the global providers"; the plan flushes the handle's providers, which are the same objects registered globally by `configure_telemetry` (or reused). This is equivalent and avoids a global lookup; instrumentor spans are still drained because they share the global tracer provider.
