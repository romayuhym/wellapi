from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from opentelemetry.metrics import Histogram, MeterProvider
    from opentelemetry.trace import Span, TracerProvider


class Telemetry:
    def __init__(
        self, tracer_provider: "TracerProvider", meter_provider: "MeterProvider"
    ):
        self.tracer_provider = tracer_provider
        self.meter_provider = meter_provider

    def span(self, name: str, **attributes: Any) -> "Span":
        return self.tracer_provider.get_tracer(__name__).start_span(
            name, attributes=attributes
        )

    def metric_histogram(self, *args, **kwargs) -> "Histogram":
        return self.meter_provider.get_meter(__name__).create_histogram(*args, **kwargs)

    def force_flush(self, timeout_millis: int = 3000) -> None:
        if hasattr(self.meter_provider, "force_flush"):
            self.meter_provider.force_flush(timeout_millis)

        if hasattr(self.tracer_provider, "force_flush"):
            self.tracer_provider.force_flush(timeout_millis)
