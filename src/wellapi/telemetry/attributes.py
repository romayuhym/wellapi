import os
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
