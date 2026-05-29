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
