# WellAPI

[![pip](https://img.shields.io/pypi/v/wellapi?color=%2334D058)](https://pypi.org/project/wellapi/)

WellAPI is a lightweight web framework for building APIs on AWS Lambda and API Gateway.

## Documentation

- Practical usage guide: [`docs/framework-usage.md`](docs/framework-usage.md)

## Features

- Lambda-first route definitions (`GET`, `POST`, `PUT`, `PATCH`, `DELETE`)
- Type-based request parsing and validation with Pydantic
- Dependency injection via `Depends`
- Middleware and custom exception handlers
- OpenAPI generation compatible with API Gateway integrations
- SQS handlers and scheduled job handlers
- Packaging helpers for Lambda app code and dependency layers

## Installation

Install the base package:

```bash
pip install wellapi
```

Install with optional extras:

```bash
pip install "wellapi[local,deploy,telemetry]"
```

- `local`: local server/test tooling
- `deploy`: AWS CDK integration
- `telemetry`: OpenTelemetry support

## Quick Start

`main.py`:

```python
from wellapi import WellApi

app = WellApi(title="My API", version="1.0.0")

@app.get("/hello")
def hello():
    return {"message": "Hello, World!"}
```

Create a `handlers/` directory for additional endpoints if needed:

```text
handlers/
  __init__.py
  users.py
```

## Local Development

Run with Uvicorn:

```bash
uvicorn main:app --reload
```

If your handlers are not in the default `handlers/` directory, configure it in `pyproject.toml`:

```bash
[wellapi]
handlers_dir = "your_handlers_dir"
```

## CLI

Generate OpenAPI:

```bash
wellapi openapi main:app handlers --output openapi.json
```

Enable CORS in OpenAPI output:

```bash
wellapi openapi main:app handlers --output openapi.json --cors true --role_name WellApiRole
```

Build deployment artifacts:

```bash
wellapi build app app.zip
wellapi build dep deps.zip
```

## Deploy with AWS CDK

```python
from aws_cdk import Stack
from constructs import Construct
from wellapi.build.cdk import WellApiCDK


class MyStack(Stack):
    def __init__(self, scope: Construct, id_: str, **kwargs) -> None:
        super().__init__(scope, id_, **kwargs)

        WellApiCDK(
            self,
            "WellApiCDK",
            app_srt="main:app",
            handlers_dir="handlers",
            cors=True,
            cache_enable=False,
            log_enable=True,
        )
```

## Notes

- API Gateway cache support applies to `GET` endpoints.
- For full framework usage (routing, DI, middleware, SQS/jobs, testing), see [`docs/framework-usage.md`](docs/framework-usage.md).
