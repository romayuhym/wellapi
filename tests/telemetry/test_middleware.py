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


def test_status_code_attribute_is_trigger_aware(
    telemetry, api_gateway_request, sqs_request, monkeypatch
):
    handle, exporter, _ = telemetry
    monkeypatch.setattr(mw_mod, "force_flush", lambda *a, **k: None)
    TelemetryMiddleware(lambda req: ResponseAPIGateway(status_code=200), handle)(
        api_gateway_request
    )
    TelemetryMiddleware(lambda req: ResponseAPIGateway(status_code=200), handle)(
        sqs_request
    )
    spans = {s.kind: s for s in exporter.get_finished_spans()}
    http_span = spans[SpanKind.SERVER]
    sqs_span = spans[SpanKind.CONSUMER]
    assert http_span.attributes["http.response.status_code"] == 200
    assert "faas.status_code" not in http_span.attributes
    assert sqs_span.attributes["faas.status_code"] == 200
    assert "http.response.status_code" not in sqs_span.attributes
