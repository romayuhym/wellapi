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
