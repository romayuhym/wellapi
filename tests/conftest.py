import pytest

from wellapi.models import RequestAPIGateway, RequestJob, RequestSQS

# A real upstream W3C trace context, reused by propagation assertions.
TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
TRACE_ID = 0x0AF7651916CD43DD8448EB211C80319C
PARENT_SPAN_ID = 0xB7AD6B7169203331


@pytest.fixture
def api_gateway_event():
    return {
        "version": "1.0",
        "resource": "/users/{id}",
        "path": "/users/123",
        "httpMethod": "GET",
        "headers": {"Host": "api.example.com", "traceparent": TRACEPARENT},
        "multiValueHeaders": {
            "Host": ["api.example.com"],
            "traceparent": [TRACEPARENT],
            "X-Custom": ["v"],
        },
        "queryStringParameters": {"q": "x"},
        "multiValueQueryStringParameters": {"q": ["x"]},
        "requestContext": {
            "accountId": "123456789012",
            "apiId": "abc",
            "domainName": "api.example.com",
            "domainPrefix": "api",
            "extendedRequestId": "ext-1",
            "httpMethod": "GET",
            "identity": {"sourceIp": "1.2.3.4"},
            "path": "/users/123",
            "protocol": "HTTP/1.1",
            "requestId": "req-1",
            "requestTime": "29/May/2026:00:00:00 +0000",
            "requestTimeEpoch": 0,
            "resourceId": "res",
            "resourcePath": "/users/{id}",
            "stage": "prod",
        },
        "pathParameters": {"id": "123"},
        "stageVariables": None,
        "body": None,
        "isBase64Encoded": False,
    }


@pytest.fixture
def sqs_event():
    return {
        "Records": [
            {
                "messageId": "m1",
                "receiptHandle": "rh",
                "body": '{"x": 1}',
                "attributes": {},
                "messageAttributes": {
                    "traceparent": {"stringValue": TRACEPARENT, "dataType": "String"}
                },
                "md5OfBody": "abc",
                "eventSource": "aws:sqs",
                "eventSourceARN": "arn:aws:sqs:eu-west-1:123456789012:my-queue",
                "awsRegion": "eu-west-1",
            }
        ]
    }


@pytest.fixture
def job_event():
    return {
        "version": "0",
        "id": "e1",
        "detail-type": "Scheduled Event",
        "source": "aws.events",
        "account": "123456789012",
        "time": "2026-05-29T00:00:00Z",
        "region": "eu-west-1",
        "resources": ["arn:aws:events:eu-west-1:123456789012:rule/nightly"],
        "detail": {},
    }


@pytest.fixture
def api_gateway_event_no_trace(api_gateway_event):
    event = dict(api_gateway_event)
    event["headers"] = {"Host": "api.example.com"}
    event["multiValueHeaders"] = {"Host": ["api.example.com"]}
    return event


@pytest.fixture
def api_gateway_request(api_gateway_event):
    return RequestAPIGateway.create_request_from_event(api_gateway_event)


@pytest.fixture
def api_gateway_request_no_trace(api_gateway_event_no_trace):
    return RequestAPIGateway.create_request_from_event(api_gateway_event_no_trace)


@pytest.fixture
def sqs_request(sqs_event):
    return RequestSQS.create_request_from_event(sqs_event)


@pytest.fixture
def job_request(job_event):
    return RequestJob.create_request_from_event(job_event)
