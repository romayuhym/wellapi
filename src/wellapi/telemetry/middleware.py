import functools
import inspect
import os
import sys
import time
import typing

from pydantic import BaseModel

try:
    from opentelemetry.trace import Span
    from opentelemetry.trace.status import Status, StatusCode
except ImportError as err:
    raise RuntimeError(
        "WellApi telemetry requires the `opentelemetry-sdk` package.\n"
        "You can install this with:\n"
        "    uv add 'wellapi[telemetry]'"
    ) from err

from wellapi.models import RequestAPIGateway, RequestJob, RequestSQS, ResponseAPIGateway
from wellapi.telemetry.telemetry import Telemetry

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
}
COLD_START = True


def get_code_attribute() -> dict[str, typing.Any]:
    _HANDLER = os.environ.get("_HANDLER")
    if not _HANDLER:
        return {}

    try:
        (mod_name, handler_name) = _HANDLER.rsplit(".", 1)

        module = sys.modules[mod_name]
        lambda_handler = getattr(module, handler_name)
        _, line_number = inspect.getsourcelines(lambda_handler)
        file_name = "/".join(mod_name.split("."))

        return {
            "code.filepath": f"{file_name}.py",
            "code.function": handler_name,
            "code.lineno": line_number,
        }
    except (ValueError, OSError, TypeError, AttributeError):
        return {}


def get_lambda_attribute() -> dict[str, typing.Any]:
    """
    lambda runtime environment variables:
    https://docs.aws.amazon.com/lambda/latest/dg/configuration-envvars.html#configuration-envvars-runtime

    semantic-conventions:
    https://github.com/open-telemetry/semantic-conventions/blob/main/docs/faas/aws-lambda.md
    """
    global COLD_START

    attribute = {
        "faas.name": os.environ.get("AWS_LAMBDA_FUNCTION_NAME", ""),
        "faas.version": os.environ.get("AWS_LAMBDA_FUNCTION_VERSION", ""),
        "cloud.region": os.environ.get("AWS_REGION", ""),
        "faas.coldstart": COLD_START,
        "faas.max_memory": os.environ.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", ""),
        "lambda.log_group": os.environ.get("AWS_LAMBDA_LOG_GROUP_NAME", ""),
        "lambda.log_stream": os.environ.get("AWS_LAMBDA_LOG_STREAM_NAME", ""),
    }

    COLD_START = False

    return attribute


class RequestAttribute(BaseModel):
    method: str
    target: str
    msg_template: str
    tag: str
    attribute: dict[str, typing.Any]


def _get_api_gateway_attribute(request: RequestAPIGateway) -> RequestAttribute:
    """
    https://github.com/open-telemetry/semantic-conventions/blob/main/docs/http/http-spans.md
    """
    method = request.raw_event.httpMethod
    target = request.raw_event.resource
    path_with_params = request.raw_event.path + (
        "?" + str(request.query_params) if request.query_params else ""
    )
    request_attribute = {
        "method": method,
        "url.path": request.raw_event.path,
        "http.request.method": method,
        "http.route": target,
        "http.url": f"https://{request.raw_event.requestContext.domainName}{path_with_params}",
        "network.peer.address": request.raw_event.requestContext.identity["sourceIp"],
        "faas.trigger": "http",
    }
    for k, v in request.headers.items():
        if k.lower() in HEADERS_TO_SKIP:
            continue

        request_attribute[f"http.request.header.{k}"] = v

    return RequestAttribute(
        method=method,
        target=target,
        msg_template="{method} {url.path}",
        tag="http",
        attribute=request_attribute,
    )


def _get_sqs_attribute(request: RequestSQS) -> RequestAttribute:
    target = request.raw_event.Records[0].eventSourceARN.split(":")[-1]

    return RequestAttribute(
        method="SQS",
        target=target,
        msg_template="{method} {messaging.destination.name}",
        tag="sqs",
        attribute={
            "method": "SQS",
            "messaging.system": "aws_sqs",
            "messaging.destination.name": target,
            "messaging.operation.name": "process",
            "messaging.operation.type": "process",
            "faas.trigger": "pubsub",
        },
    )


def _get_job_attribute(_: RequestJob) -> RequestAttribute:
    return RequestAttribute(
        method="JOB",
        target=os.environ.get("JOB_NAME", ""),
        msg_template="{method} {job.name} [{faas.cron}]",
        tag="job",
        attribute={
            "method": "JOB",
            "job.name": os.environ.get("JOB_NAME", ""),
            "faas.cron": os.environ.get("SCHEDULE_EXPRESSION", ""),
            "faas.trigger": "timer",
        },
    )


def get_request_attribute(
    request: RequestAPIGateway | RequestSQS | RequestJob,
) -> RequestAttribute:
    """
    https://github.com/open-telemetry/semantic-conventions/blob/main/docs/faas/aws-lambda.md
    """
    if isinstance(request, RequestAPIGateway):
        return _get_api_gateway_attribute(request)
    elif isinstance(request, RequestSQS):
        return _get_sqs_attribute(request)
    elif isinstance(request, RequestJob):
        return _get_job_attribute(request)
    else:
        return RequestAttribute(
            method="_OTHER",
            target="unknown",
            msg_template="Unknown Request",
            tag="unknown",
            attribute={},
        )


class TelemetryMiddleware:
    def __init__(
        self,
        next_call: typing.Callable,
        telemetry: Telemetry,
        request_hook: typing.Callable[[Span, RequestAPIGateway], None] | None = None,
        response_hook: typing.Callable[[Span, ResponseAPIGateway | None], None] | None = None,
    ) -> None:
        self.next_call = next_call
        self.telemetry = telemetry
        self.request_hook = request_hook
        self.response_hook = response_hook
        functools.update_wrapper(self, next_call, updated=())

        self.metric = self.telemetry.metric_histogram(
            name="http.server.duration",
            unit="ms",
            description="Measures the duration of inbound HTTP requests.",
        )

    def __call__(
        self, request: RequestAPIGateway | RequestJob | RequestSQS
    ) -> ResponseAPIGateway:
        start = time.perf_counter()

        code_info = get_code_attribute()
        lambda_attribute = get_lambda_attribute()
        request_attribute = get_request_attribute(request)

        with self.telemetry.span(
            request_attribute.msg_template,
            **request_attribute.attribute,
            **code_info,
            **lambda_attribute,
            _tags=[request_attribute.tag],
        ) as span:
            if self.request_hook:
                self.request_hook(span, request)

            response = None
            exception = None
            try:
                response: ResponseAPIGateway = self.next_call(request)
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

        duration = time.perf_counter() - start
        self.metric.record(
            max(round(duration * 1000), 0),
            {
                "http.method": request_attribute.method,
                "http.target": request_attribute.target,
                "http.status_code": status_code,
            },
        )
        self.telemetry.force_flush()

        if exception:
            raise exception.with_traceback(exception.__traceback__)

        return response
