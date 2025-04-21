# WellAPI

WellAPI is a simple web framework for work with AWS Lambda
and API Gateway. It is designed to be easy to use and flexible, allowing you to create RESTful APIs quickly and efficiently.

## Features
- Simple and intuitive API for defining routes and handling requests
- Support for middleware functions
- Automatic request validation and error handling
- Support for CORS
- Support for query parameters, path parameters, and request bodies
- Support for error handling
- Support for AWS Lambda and API Gateway

## Installation

You can install WellAPI using pip:

```bash
pip instsll wellapi
```

## Example

```python
from wellapi import WellApi

app = WellApi()

@app.get("/hello")
def hello():
    return {"message": "Hello, World!"}
```

## Local development

You can run your WellAPI application locally using the `wellapi` command:

```bash
wellapi run main:app
```


## Deploy

```python
from wellapi.build.cdk import WellApiCDK


class MyStack(Stack):
    def __init__(self, scope: Construct, id_: str, **kwargs) -> None:
        super().__init__(scope, id_, **kwargs)

        app = WellApiCDK(
            self,
            "WellApiCDK",
            app_srt="main:app",
            handlers_dir="handlers",
        )
```

### CORS
You can enable CORS for your API by setting the `cors` parameter to `True` when creating the `WellApiCDK` instance:

```python
from wellapi.build.cdk import WellApiCDK


class MyStack(Stack):
    def __init__(self, scope: Construct, id_: str, **kwargs) -> None:
        super().__init__(scope, id_, **kwargs)

        app = WellApiCDK(
            self,
            "WellApiCDK",
            app_srt="main:app",
            handlers_dir="handlers",
            cors=True,
        )
```

### Cache

You can enable caching for your API by setting the `cache_enable` parameter to `True` when creating the `WellApiCDK` instance:

```python
from wellapi.build.cdk import WellApiCDK


class MyStack(Stack):
    def __init__(self, scope: Construct, id_: str, **kwargs) -> None:
        super().__init__(scope, id_, **kwargs)

        app = WellApiCDK(
            self,
            "WellApiCDK",
            app_srt="main:app",
            handlers_dir="handlers",
            cache_enable=True,
        )
```
**!!! WARNING !!!**

Caching support only GET endpoints

### Logging

You can enable logging for your API by setting the `log_enable` parameter to `True` when creating the `WellApiCDK` instance:

```python
from wellapi.build.cdk import WellApiCDK


class MyStack(Stack):
    def __init__(self, scope: Construct, id_: str, **kwargs) -> None:
        super().__init__(scope, id_, **kwargs)

        app = WellApiCDK(
            self,
            "WellApiCDK",
            app_srt="main:app",
            handlers_dir="handlers",
            log_enable=True,
        )
```