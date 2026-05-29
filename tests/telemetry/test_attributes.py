from wellapi.telemetry.attributes import RequestAttribute, get_request_attribute


def test_api_gateway_span_name_uses_route_not_path(api_gateway_request):
    attr = get_request_attribute(api_gateway_request)
    assert isinstance(attr, RequestAttribute)
    assert attr.span_name == "GET /users/{id}"  # route template, never /users/123
    assert "123" not in attr.span_name


def test_api_gateway_semconv_attributes(api_gateway_request):
    attr = get_request_attribute(api_gateway_request)
    a = attr.attributes
    assert attr.kind == "SERVER"
    assert attr.trigger == "http"
    assert attr.method == "GET"
    assert attr.route == "/users/{id}"
    assert a["http.request.method"] == "GET"
    assert a["http.route"] == "/users/{id}"
    assert a["url.path"] == "/users/123"
    assert a["faas.trigger"] == "http"
    assert a["network.peer.address"] == "1.2.3.4"


def test_api_gateway_header_attributes_skip_sensitive_and_trace(api_gateway_request):
    a = get_request_attribute(api_gateway_request).attributes
    assert a["http.request.header.x-custom"] == "v"
    assert "http.request.header.host" not in a
    assert "http.request.header.traceparent" not in a


def test_sqs_attributes(sqs_request):
    attr = get_request_attribute(sqs_request)
    assert attr.span_name == "my-queue process"
    assert attr.kind == "CONSUMER"
    assert attr.trigger == "sqs"
    assert attr.route == "my-queue"
    a = attr.attributes
    assert a["messaging.system"] == "aws_sqs"
    assert a["messaging.destination.name"] == "my-queue"
    assert a["faas.trigger"] == "pubsub"


def test_job_attributes(job_request, monkeypatch):
    monkeypatch.setenv("JOB_NAME", "nightly")
    monkeypatch.setenv("SCHEDULE_EXPRESSION", "rate(1 day)")
    attr = get_request_attribute(job_request)
    assert attr.span_name == "nightly"
    assert attr.kind == "SERVER"
    assert attr.trigger == "job"
    assert attr.route == "nightly"
    a = attr.attributes
    assert a["job.name"] == "nightly"
    assert a["faas.cron"] == "rate(1 day)"
    assert a["faas.trigger"] == "timer"
