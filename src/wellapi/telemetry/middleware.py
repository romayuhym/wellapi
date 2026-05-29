import functools
import time
import typing

try:
    from opentelemetry import propagate
    from opentelemetry.context import Context
    from opentelemetry.trace import Link, Span, SpanKind, get_current_span
    from opentelemetry.trace.status import Status, StatusCode
except ImportError as err:  # pragma: no cover
    raise RuntimeError(
        "WellApi telemetry requires the `opentelemetry-sdk` package.\n"
        "You can install this with:\n"
        "    uv add 'wellapi[telemetry]'"
    ) from err

from wellapi.models import RequestAPIGateway, RequestJob, RequestSQS, ResponseAPIGateway
from wellapi.telemetry.attributes import (
    RequestAttribute,
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
        # Each invocation starts its OWN trace (root span). The inbound trace
        # context is attached as a link, not used as the parent: a sticky or
        # shared upstream traceparent would otherwise collapse unrelated
        # invocations into a single trace_id (warm-container fan-in).
        upstream = get_current_span(
            propagate.extract(get_trace_carrier(request))
        ).get_span_context()
        links = [Link(upstream)] if upstream.is_valid else None
        span_attributes = {
            **attribute.attributes,
            **get_code_attribute(),
            **get_invocation_attribute(),
        }

        exception: Exception | None = None
        response: ResponseAPIGateway | None = None
        with self.tracer.start_as_current_span(
            attribute.span_name,
            context=Context(),
            kind=_SPAN_KIND.get(attribute.kind, SpanKind.SERVER),
            attributes=span_attributes,
            links=links,
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
                # Status code lives under the HTTP semconv key only on HTTP
                # spans; SQS/Job spans use a FaaS-specific key.
                if attribute.trigger == "http":
                    span.set_attribute("http.response.status_code", status_code)
                else:
                    span.set_attribute("faas.status_code", status_code)

            if self.response_hook:
                self.response_hook(span, response)

        self._record_metric(attribute, status_code, time.perf_counter() - start)
        force_flush(self.handle)

        if exception:
            raise exception.with_traceback(exception.__traceback__)

        return response

    def _record_metric(
        self, attribute: RequestAttribute, status_code: int, duration_s: float
    ) -> None:
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
