import json
import logging
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from wellapi.local.reloader import run_with_reloader
from wellapi.local.router import Router


def get_request_handler(router: Router):
    class RequestHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self._handle_request("GET")

        def do_POST(self):
            self._handle_request("POST")

        def do_PUT(self):
            self._handle_request("PUT")

        def do_DELETE(self):
            self._handle_request("DELETE")

        def _handle_request(self, method):
            path = self.path.split("?")[0]  # Видаляємо query параметри

            # Створюємо мок для AWS Lambda event
            content_length = int(self.headers.get("Content-Length", 0))
            body = (
                self.rfile.read(content_length).decode() if content_length > 0 else None
            )

            if self.path.startswith("/job_"):
                event = self.create_job_event()
            elif self.path.startswith("/queue_"):
                event = self.create_queue_event(body)
            else:
                event = self.create_api_event(method, path, body)

            try:
                result = router(event, method, path)

                self.send_response(result["statusCode"])
                for key, value in result["headers"].items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(result["body"].encode())
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())

        def create_job_event(self):
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

        def create_queue_event(self, body):
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

        def create_api_event(self, method, path, body):
            headers = {}
            for key, value in self.headers.items():
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
            if "?" in self.path:
                query_string = self.path.split("?")[1]
                query_params = {}
                for param in query_string.split("&"):
                    if "=" in param:
                        key, value = param.split("=")
                        query_params.setdefault(key, []).append(value)
                event["multiValueQueryStringParameters"] = query_params
            else:
                event["multiValueQueryStringParameters"] = {}

            return event

    return RequestHandler


class Server:
    def __init__(self, app_srt, handlers_dir, host, port):
        self.router = Router()
        self.app_srt = app_srt
        self.handlers_dir = handlers_dir
        self.server = HTTPServer((host, port), get_request_handler(self.router))

        self.handlers_module = handlers_dir.split("/")[-1]
        self.app_module = app_srt.split(":")[0]

    def start_server(self):
        self.router.discover_handlers(self.app_srt, self.handlers_dir)

        logging.info(
            f"Starting server on {self.server.server_address[0]}:{self.server.server_address[1]}"
        )
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def on_reload(self):
        for module_name in list(sys.modules.keys()):
            if self.handlers_module in module_name or module_name == self.app_module:
                del sys.modules[module_name]

        self.router.discover_handlers(self.app_srt, self.handlers_dir)


def run_local_server(app_srt, handlers_dir, host, port, autoreload):
    server = Server(app_srt, handlers_dir, host, port)

    if autoreload:
        run_with_reloader(server)
    else:
        server.start_server()
