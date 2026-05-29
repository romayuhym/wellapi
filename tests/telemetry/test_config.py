import wellapi.telemetry.config as config_mod
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
