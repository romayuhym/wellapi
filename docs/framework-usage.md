# WellAPI Framework Usage Guide

This guide explains how to use WellAPI based on the current codebase.

## 1. What WellAPI Provides

WellAPI is designed for AWS Lambda + API Gateway and includes:

- route decorators (`get`, `post`, `put`, `patch`, `delete`);
- request validation using Python typing and Pydantic;
- dependency injection with `Depends`;
- security schemes (`APIKeyHeader`, `OAuth2PasswordBearer`);
- middleware and custom exception handlers;
- SQS handlers (`@app.sqs`) and scheduled jobs (`@app.job`);
- OpenAPI generation for API Gateway;
- zip packaging for app code and dependencies.

## 2. Installation

Minimal install:

```bash
pip install wellapi
```

Recommended for local dev + deploy + telemetry:

```bash
pip install "wellapi[local,deploy,telemetry]"
```

Python requirement: `3.12+`.

## 3. Minimal Project Layout

```text
project/
  main.py
  handlers/
    __init__.py
    users.py
```

- `main.py` defines `app = WellApi(...)`.
- Files in `handlers/` are imported to register routes.

## 4. Quick Start

`main.py`:

```python
from wellapi import RequestAPIGateway, WellApi

app = WellApi(
    title="My API",
    version="1.0.0",
    debug=True,
    servers=[{"url": "http://localhost:8000", "description": "Local"}],
)


@app.get("/health")
def health(request: RequestAPIGateway):
    return {"ok": True, "method": request.raw_event.httpMethod}
```

`handlers/users.py`:

```python
from typing import Annotated

from pydantic import BaseModel, Field

from main import app
from wellapi import Header, Query


class UserOut(BaseModel):
    id: int
    name: str


class ListQuery(BaseModel):
    limit: int = Field(20, ge=1, le=100)
    offset: int = Field(0, ge=0)


@app.get("/users/{user_id:int}")
def get_user(
    user_id: int,
    trace_id: Annotated[str | None, Header(alias="x-trace-id")] = None,
) -> UserOut:
    return UserOut(id=user_id, name=f"user-{trace_id or 'n/a'}")


@app.get("/users")
def list_users(params: Annotated[ListQuery, Query()]):
    return {"limit": params.limit, "offset": params.offset}
```

## 5. Parameters, Body, and Responses

### Parameters

- Path params come from route templates like `{id}` or `{id:int}`.
- Query/header params can be declared with `Annotated[..., Query()]` and `Annotated[..., Header()]`.
- `Header` converts underscores to hyphens by default.

### Request Body

Complex types (Pydantic models, lists, dicts) are treated as JSON body.

```python
from pydantic import BaseModel


class CreateUser(BaseModel):
    name: str
    age: int


@app.post("/users", status_code=201)
def create_user(item: CreateUser):
    return {"id": 1, **item.model_dump()}
```

### Response Model

You can set `response_model` explicitly or rely on return annotations:

```python
@app.get("/profile")
def profile() -> UserOut:
    return UserOut(id=7, name="Jane")
```

## 6. Dependencies and Security

```python
from typing import Annotated

from wellapi.exceptions import HTTPException
from wellapi.params import Depends
from wellapi.security import APIKeyHeader, OAuth2PasswordBearer

api_key_scheme = APIKeyHeader(name="x-api-key")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


def verify_token(token: Annotated[str, Depends(oauth2_scheme)]):
    if token != "fake-token":
        raise HTTPException(status_code=403, detail="Not authenticated")


@app.post("/secure-api-key", dependencies=[Depends(api_key_scheme)])
def secure_api_key():
    return {"ok": True}


@app.post("/secure-bearer", dependencies=[Depends(verify_token)])
def secure_bearer():
    return {"ok": True}
```

## 7. Middleware and Exception Handlers

```python
from wellapi.exceptions import RequestValidationError
from wellapi.models import RequestAPIGateway, ResponseAPIGateway


@app.middleware()
def log_middleware(request: RequestAPIGateway, next_call) -> ResponseAPIGateway:
    response = next_call(request)
    response.headers["x-powered-by"] = "wellapi"
    return response


@app.exception_handler(RequestValidationError)
def validation_exception_handler(request, exc):
    return ResponseAPIGateway({"detail": exc.errors()}, status_code=422)
```

## 8. SQS and Scheduled Jobs

```python
from typing import Annotated

from pydantic import BaseModel
from wellapi.params import Depends


class QueueEvent(BaseModel):
    name: str
    age: int


@app.sqs("users-queue", batch_size=10)
def process_users(events: list[QueueEvent]):
    for event in events:
        print(event.name)


def db_session():
    return "db-session"


@app.job("rate(5 minutes)", name="sync_users")
def sync_users(db: Annotated[str, Depends(db_session)]):
    print(db)
```

For local testing with `TestClient`:

- SQS handler path is `POST /queue_/users-queue`.
- Job handler path is `POST /job_/sync_users`.

## 9. Generate OpenAPI

```bash
wellapi openapi main:app handlers --output openapi.json
```

With CORS and custom API Gateway role:

```bash
wellapi openapi main:app handlers --output openapi.json --cors true --role_name WellApiRole
```

Important:

- `main:app` points to `main.py` and variable `app`.
- `handlers` points to your handlers directory.

## 10. Package for AWS Lambda

App code zip:

```bash
wellapi build app app.zip
```

Dependencies zip (Lambda layer):

```bash
wellapi build dep deps.zip
```

## 11. AWS CDK Integration

```python
from aws_cdk import Stack
from constructs import Construct
from wellapi.build.cdk import WellApiCDK


class ApiStack(Stack):
    def __init__(self, scope: Construct, id_: str, **kwargs):
        super().__init__(scope, id_, **kwargs)

        WellApiCDK(
            self,
            "WellApi",
            app_srt="main:app",
            handlers_dir="handlers",
            cors=True,
            cache_enable=False,
            log_enable=True,
        )
```

## 12. Local Testing Without a Server

```python
from wellapi.testclient import TestClient

from main import app
import handlers.users  # ensure routes are registered

client = TestClient(app)
response = client.get("/health")
assert response.status_code == 200
```

## 13. Practical Notes

- Path converters supported: `str`, `path`, `int`, `float`, `uuid`.
- Security dependencies are reflected in OpenAPI `components.securitySchemes`.
- API Gateway cache keys for GET routes can be configured with `cache_parameters=Cache(...)`.
- In the current CLI implementation, the available commands are `openapi` and `build`.
