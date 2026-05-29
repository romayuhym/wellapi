import os
from dataclasses import dataclass
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry._logs import get_logger_provider, set_logger_provider

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
