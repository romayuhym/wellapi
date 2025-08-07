import json
import tomllib

from wellapi.local.router import Router

ROUTER = None


def get_app_config() -> dict:
    with open("pyproject.toml", "rb") as f:
        data = tomllib.load(f)

    return data.get("wellapi", {})


async def handel_local(scope, receive, send):
    global ROUTER

    if scope["type"] == "lifespan":
        conf = get_app_config()
        ROUTER = Router(scope["app"], conf.get("handlers_dir"))
        return

    body = await get_body(receive)
    method = scope["method"]
    path = scope["path"]

    if path.startswith("/job_"):
        event = create_job_event()
    elif path.startswith("/queue_"):
        event = create_queue_event(body)
    else:
        headers = {h_k.decode(): h_v.decode() for h_k, h_v in list(scope["headers"])}
        event = create_api_event(method, path, body, headers, scope["query_string"].decode())

    try:
        result = ROUTER(event, method, path)

        await send(
            {
                "type": "http.response.start",
                "status": result["statusCode"],
                "headers": [
                    [key.encode(), value.encode()]
                    for key, value in result["headers"].items()
                ],
            }
        )
        await send({"type": "http.response.body", "body": result["body"].encode()})
    except Exception as e:
        await send(
            {
                "type": "http.response.start",
                "status": 500,
                "headers": [
                    [b"content-type", b"application/json"],
                ],
            }
        )
        await send({"type": "http.response.body",
                    "body": json.dumps({"error": str(e)}).encode()})


async def get_body(receive):
    async def stream():
        stream_consumed = False
        while not stream_consumed:
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                if not message.get("more_body", False):
                    stream_consumed = True
                if body:
                    yield body

        yield b""

    chunks: list[bytes] = []
    async for chunk in stream():
        chunks.append(chunk)
    return b"".join(chunks)


def create_job_event():
    return {
        "version": "0",
        "id": "53dc4d37-cffa-4f76-80c9-8b7d4a4d2eaa",
        "detail-type": "Scheduled Event",
        "source": "aws.events",
        "account": "123456789012",
        "time": "2015-10-08T16:53:06Z",
        "region": "us-east-1",
        "resources": [
            "arn:aws:events:us-east-1:123456789012:rule/my-scheduled-rule"
        ],
        "detail": {},
    }


def create_queue_event(body):
    record_template = {
        "messageId": "059f36b4-87a3-44ab-83d2-661975830a7d",
        "receiptHandle": "AQEBwJnKyrHigUMZj6rYigCgxlaS3SLy0a...",
        "body": "test",
        "attributes": {
            "ApproximateReceiveCount": "1",
            "SentTimestamp": "1545082649183",
            "SenderId": "AIDAIENQZJOLO23YVJ4VO",
            "ApproximateFirstReceiveTimestamp": "1545082649185",
        },
        "messageAttributes": {},
        "md5OfBody": "098f6bcd4621d373cade4e832627b4f6",
        "eventSource": "aws:sqs",
        "eventSourceARN": "arn:aws:sqs:us-east-1:111122223333:my-queue",
        "awsRegion": "us-east-1",
    }
    body = json.loads(body)
    if isinstance(body, dict):
        return {"Records": [record_template | {"body": json.dumps(body)}]}
    if isinstance(body, list):
        return {
            "Records": [record_template | {"body": json.dumps(b)} for b in body]
        }


def create_api_event(method, path, body, headers_row, query_string):
    headers = {}
    for key, value in headers_row.items():
        headers.setdefault(key, []).append(value)

    event = {
        "version": "1.0",
        "resource": "/my/path",
        "httpMethod": method,
        "path": path,
        "multiValueHeaders": headers,
        "body": body,
        "headers": {},
        "queryStringParameters": {},
        "requestContext": {
            'resourceId': 'zdo27u',
            'resourcePath': path,
            'operationName': 'main.hello',
            'httpMethod': method,
            'extendedRequestId': 'J449EG3OliAEceQ=',
            'requestTime': '01/May/2025:12:53:13 +0000',
            'path': '/prod/hello',
            'accountId': '125905311728',
            'protocol': 'HTTP/1.1',
            'stage': 'prod',
            'domainPrefix': 'pxeuu259g4',
            'requestTimeEpoch': 1746103993615,
            'requestId': '00cc795f-6b70-4f4d-9d7f-1800b9af134e',
            'identity': {},
            'domainName': 'pxeuu259g4.execute-api.eu-central-1.amazonaws.com',
            'deploymentId': 'q4efka',
            'apiId': 'pxeuu259g4'
        },
        "pathParameters": None,
        "stageVariables": None,
        "isBase64Encoded": False,
    }

    # Парсимо query параметри
    if query_string:
        query_params = {}
        for param in query_string.split("&"):
            if "=" in param:
                key, value = param.split("=")
                query_params.setdefault(key, []).append(value)
        event["multiValueQueryStringParameters"] = query_params
    else:
        event["multiValueQueryStringParameters"] = {}

    return event
