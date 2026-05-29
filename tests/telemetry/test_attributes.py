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
