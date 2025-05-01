from pydantic import BaseModel, Field


class RequestContext(BaseModel):
    accountId: str
    apiId: str
    authorizer: dict = None
    domainName: str
    domainPrefix: str
    extendedRequestId: str
    httpMethod: str
    identity: dict
    path: str
    protocol: str
    requestId: str
    requestTime: str
    requestTimeEpoch: int
    resourceId: str
    resourcePath: str
    stage: str


class ApiGatewayEvent(BaseModel):
    """
    https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-develop-integrations-lambda.html#http-api-develop-integrations-lambda.proxy-format
    """
    version: str = "1.0"
    resource: str
    path: str
    httpMethod: str
    headers: dict
    multiValueHeaders: dict
    queryStringParameters: dict | None
    multiValueQueryStringParameters: dict | None
    requestContext: RequestContext
    pathParameters: dict | None
    stageVariables: dict | None
    body: str | None
    isBase64Encoded: bool



class Message(BaseModel):
    messageId: str
    receiptHandle: str
    body: str
    attributes: dict
    messageAttributes: dict
    md5OfBody: str
    eventSource: str
    eventSourceARN: str
    awsRegion: str


class SQSEvent(BaseModel):
    """
    https://docs.aws.amazon.com/lambda/latest/dg/with-sqs-example.html#with-sqs-create-test-function
    """
    Records: list[Message]


class JobEvent(BaseModel):
    """
    https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-run-lambda-schedule.html#eb-schedule-create-rule
    """
    version: str
    id: str
    detail_type: str = Field(alias="detail-type")
    source: str
    account: str
    time: str
    region: str
    resources: list[str]
    detail: dict
