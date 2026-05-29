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
