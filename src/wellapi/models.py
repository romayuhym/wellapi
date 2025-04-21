import json
import typing

from wellapi.datastructures import Headers, MutableHeaders, QueryParams


class RequestAPIGateway:
    """
    https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-develop-integrations-lambda.html#http-api-develop-integrations-lambda.proxy-format
    """

    def __init__(self, path_params, query_params, headers, body, cookies):
        self.path_params = path_params
        self.query_params = QueryParams(query_params)
        self.headers = Headers(raw=headers)
        self.cookies = cookies
        self._body = body

    def json(self):
        """
        Returns the request body as JSON.
        """
        if not self._body:
            return None

        return json.loads(self._body)

    @classmethod
    def create_request_from_event(cls, event):
        """
        Create a RequestAPIGateway object from the AWS API Gateway event.
        """
        path_params = event.get("pathParameters", {})
        multi_query_params = event.get("multiValueQueryStringParameters", {}) or {}
        multi_headers = event.get("multiValueHeaders", {}) or {}
        body = event.get("body", "")
        cookies = event.get("cookies", {})

        headers = []
        for h_name, h_value in multi_headers.items():
            if isinstance(h_value, list):
                for h in h_value:
                    headers.append(
                        (h_name.lower().encode("latin-1"), h.encode("latin-1"))
                    )
            else:
                headers.append(
                    (h_name.lower().encode("latin-1"), h_value.encode("latin-1"))
                )

        query_params = []
        for q_name, q_value in multi_query_params.items():
            if isinstance(q_value, list):
                for q in q_value:
                    query_params.append((q_name, q))
            else:
                query_params.append((q_name, q_value))

        return cls(path_params, query_params, headers, body, cookies)


class ResponseAPIGateway:
    isBase64Encoded: bool = False

    def __init__(
        self,
        content: typing.Any = None,
        status_code: int = 200,
        headers: typing.Mapping[str, str] | None = None,
    ) -> None:
        self.statusCode = status_code
        self.body = content
        self.raw_headers = headers

    @property
    def headers(self) -> MutableHeaders:
        if not hasattr(self, "_headers"):
            self._headers = MutableHeaders(headers=self.raw_headers)
        return self._headers

    def to_aws_response(self):
        if isinstance(self.body, str):
            body = self.body
        else:
            body = json.dumps(self.body)

        return {
            "statusCode": self.statusCode,
            "headers": dict(self.headers),
            "body": body,
            "isBase64Encoded": self.isBase64Encoded,
        }


class RequestSQS:
    """
    https://docs.aws.amazon.com/lambda/latest/dg/with-sqs-example.html#with-sqs-create-test-function
    """

    def __init__(self, records: list[dict[str, typing.Any]]):
        self._records = records
        self.path_params = None
        self.query_params = None
        self.headers = None
        self.cookies = None

    @classmethod
    def create_request_from_event(cls, event):
        """
        Create a RequestAPIGateway object from the AWS API Gateway event.
        """

        return cls(event["Records"])

    def json(self):
        """
        Returns the request body as JSON.
        """
        if not self._records:
            return None

        body = []
        for record in self._records:
            body.append(json.loads(record.get("body")))

        return body


class RequestJob:
    """
    https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-run-lambda-schedule.html#eb-schedule-create-rule
    """

    def __init__(self, event: dict[str, typing.Any]):
        self._event = event
        self.path_params = None
        self.query_params = None
        self.headers = None
        self.cookies = None

    @classmethod
    def create_request_from_event(cls, event):
        """
        Create a RequestAPIGateway object from the AWS API Gateway event.
        """

        return cls(event)

    def json(self):
        return None
