import inspect
from collections.abc import Callable, Sequence
from enum import Enum
from typing import Any, Literal

from pydantic._internal._utils import lenient_issubclass
from pydantic.main import IncEx

from wellapi import params
from wellapi.datastructures import Default, DefaultPlaceholder
from wellapi.dependencies.models import ModelField
from wellapi.dependencies.utils import (
    _should_embed_body_fields,
    create_model_field,
    get_body_field,
    get_dependant,
    get_flat_dependant,
    get_parameterless_sub_dependant,
    get_typed_return_annotation,
)
from wellapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from wellapi.exceptions import HTTPException, RequestValidationError
from wellapi.middleware.base import BaseMiddleware
from wellapi.middleware.error import ServerErrorMiddleware
from wellapi.middleware.exceptions import ExceptionMiddleware
from wellapi.middleware.main import Middleware
from wellapi.models import RequestAPIGateway, ResponseAPIGateway
from wellapi.routing import (
    compile_path,
    get_request_handler,
    is_body_allowed_for_status_code,
    request_response,
)


def to_camel_case(snake_str):
    return "".join(x.capitalize() for x in snake_str.lower().split("_"))


def get_arn(endpoint: Callable[..., Any]) -> str:
    name = f"{endpoint.__module__}.{endpoint.__name__}"
    name = name.replace(".", "_")
    return to_camel_case(name)


class Lambda:
    def __init__(
        self,
        path: str,
        endpoint: Callable[..., Any],
        *,
        method: str = None,
        type_: Literal["endpoint", "queue", "job"] = "endpoint",
        name: str | None = None,
        memory_size: int = 128,
        timeout: int = 30,
        response_model: Any = Default(None),
        status_code: int | None = None,
        description: str | None = None,
        cache_parameters: params.Cache | None = None,
        response_description: str = "Successful Response",
        responses: dict[int | str, dict[str, Any]] | None = None,
        response_class: type[ResponseAPIGateway] | DefaultPlaceholder = Default(
            ResponseAPIGateway
        ),
        response_model_include: IncEx | None = None,
        response_model_exclude: IncEx | None = None,
        response_model_by_alias: bool = True,
        response_model_exclude_unset: bool = False,
        response_model_exclude_defaults: bool = False,
        response_model_exclude_none: bool = False,
        dependencies: Sequence[params.Depends] | None = None,
        tags: list[str | Enum] | None = None,
        deprecated: bool | None = None,
        operation_id: str | None = None,
        summary: str | None = None,
    ):
        self.path = path
        self.type_ = type_
        self.name = endpoint.__name__ if name is None else name
        self.memory_size = memory_size
        self.timeout = timeout
        self.method = method
        self.endpoint = endpoint
        self.cache_parameters = cache_parameters
        self.path_regex, self.path_format, self.param_convertors = compile_path(path)
        self.dependencies = list(dependencies or [])
        self.operation_id = operation_id
        self.unique_id = (
            self.operation_id or f"{self.endpoint.__module__}.{self.endpoint.__name__}"
        )
        self.status_code = status_code
        self.response_description = response_description
        self.response_class = response_class
        self.response_model_include = response_model_include
        self.response_model_exclude = response_model_exclude
        self.response_model_by_alias = response_model_by_alias
        self.response_model_exclude_unset = response_model_exclude_unset
        self.response_model_exclude_defaults = response_model_exclude_defaults
        self.response_model_exclude_none = response_model_exclude_none
        self.tags = tags or []
        self.deprecated = deprecated
        self.summary = summary
        self.arn = get_arn(self.endpoint)

        if isinstance(response_model, DefaultPlaceholder):
            return_annotation = get_typed_return_annotation(endpoint)
            if lenient_issubclass(return_annotation, ResponseAPIGateway):
                response_model = None
            else:
                response_model = return_annotation
        self.response_model = response_model
        if self.response_model:
            assert is_body_allowed_for_status_code(status_code), (
                f"Status code {status_code} must not have a response body"
            )
            response_name = "Response_" + self.unique_id
            self.response_field = create_model_field(
                name=response_name,
                type_=self.response_model,
                mode="serialization",
            )
        else:
            self.response_field = None  # type: ignore
            self.secure_cloned_response_field = None

        self.description = description or inspect.cleandoc(self.endpoint.__doc__ or "")
        # if a "form feed" character (page break) is found in the description text,
        # truncate description text to the content preceding the first "form feed"
        self.description = self.description.split("\f")[0].strip()
        response_fields = {}
        self.responses = responses or {}
        for additional_status_code, response in self.responses.items():
            assert isinstance(response, dict), "An additional response must be a dict"
            model = response.get("model")
            if model:
                assert is_body_allowed_for_status_code(additional_status_code), (
                    f"Status code {additional_status_code} must not have a response body"
                )
                response_name = f"Response_{additional_status_code}_{self.unique_id}"
                response_field = create_model_field(
                    name=response_name, type_=model, mode="serialization"
                )
                response_fields[additional_status_code] = response_field
        if response_fields:
            self.response_fields: dict[int | str, ModelField] = response_fields
        else:
            self.response_fields = {}

        assert callable(endpoint), "An endpoint must be a callable"
        self.dependant = get_dependant(
            path=self.path_format, call=self.endpoint, type_=self.type_
        )
        for depends in self.dependencies[::-1]:
            self.dependant.dependencies.insert(
                0,
                get_parameterless_sub_dependant(
                    depends=depends, path=self.path_format, type_=self.type_
                ),
            )
        self._flat_dependant = get_flat_dependant(self.dependant)
        self._embed_body_fields = _should_embed_body_fields(
            self._flat_dependant.body_params
        )
        self.body_field = get_body_field(
            flat_dependant=self._flat_dependant,
            name=self.unique_id,
            embed_body_fields=self._embed_body_fields,
        )
        self.app = self.get_route_handler()

    def get_route_handler(self):
        return get_request_handler(
            dependant=self.dependant,
            status_code=self.status_code,
            response_class=self.response_class,
            response_field=self.response_field,
            response_model_include=self.response_model_include,
            response_model_exclude=self.response_model_exclude,
            response_model_by_alias=self.response_model_by_alias,
            response_model_exclude_unset=self.response_model_exclude_unset,
            response_model_exclude_defaults=self.response_model_exclude_defaults,
            response_model_exclude_none=self.response_model_exclude_none,
            embed_body_fields=self._embed_body_fields,
        )


class WellApi:
    def __init__(
        self,
        title: str = "WellApi",
        version: str = "0.1.0",
        description: str = "",
        openapi_tags: list[dict[str, Any]] | None = None,
        servers: list[dict[str, str | Any]] | None = None,
        queues: list[dict[str, Any]] | None = None,
        debug: bool = False,
    ):
        self.lambdas = []
        self.exception_handlers = {}
        self.exception_handlers.setdefault(HTTPException, http_exception_handler)
        self.exception_handlers.setdefault(
            RequestValidationError, request_validation_exception_handler
        )
        self.user_middleware = []
        self.debug = debug
        self.title = title
        self.version = version
        self.description = description
        self.openapi_tags = openapi_tags
        self.servers = servers
        self.queues = queues or []

    def build_middleware_stack(self, app: Callable) -> Callable:
        debug = self.debug
        error_handler = None
        exception_handlers: dict[
            Any, Callable[[RequestAPIGateway, Exception], ResponseAPIGateway]
        ] = {}

        for key, value in self.exception_handlers.items():
            if key in (500, Exception):
                error_handler = value
            else:
                exception_handlers[key] = value

        middleware = (
            [Middleware(ServerErrorMiddleware, handler=error_handler, debug=debug)]
            + self.user_middleware
            + [
                Middleware(
                    ExceptionMiddleware, handlers=exception_handlers, debug=debug
                )
            ]
        )

        for cls, args, kwargs in reversed(middleware):
            app = cls(app, *args, **kwargs)
        return request_response(app)

    def add_endpoint(self, *args, **kwargs):
        lambda_ = Lambda(*args, **kwargs)
        self.lambdas.append(lambda_)

        def endpoint(event, context):
            return self.build_middleware_stack(lambda_.app)(event, context)

        return endpoint

    def add_exception_handler(
        self,
        exc_class_or_status_code: int | type[Exception],
        handler: Callable[[RequestAPIGateway, Exception], ResponseAPIGateway],
    ) -> None:  # pragma: no cover
        self.exception_handlers[exc_class_or_status_code] = handler

    def exception_handler(
        self, exc_class_or_status_code: int | type[Exception]
    ) -> Callable:
        def decorator(func):
            self.add_exception_handler(exc_class_or_status_code, func)
            return func

        return decorator

    def add_middleware(self, middleware_class, dispatch) -> None:
        self.user_middleware.insert(0, Middleware(middleware_class, dispatch))

    def middleware(self) -> Callable:
        def decorator(func):
            self.add_middleware(BaseMiddleware, dispatch=func)
            return func

        return decorator

    def get(
        self,
        path: str,
        *,
        response_model: Any = Default(None),
        status_code: int | None = None,
        cache_parameters: params.Cache | None = None,
        memory_size: int = 128,
        timeout: int = 30,
        response_class: type[ResponseAPIGateway] | DefaultPlaceholder = Default(
            ResponseAPIGateway
        ),
        response_model_include: IncEx | None = None,
        response_model_exclude: IncEx | None = None,
        response_model_by_alias: bool = True,
        response_model_exclude_unset: bool = False,
        response_model_exclude_defaults: bool = False,
        response_model_exclude_none: bool = False,
        dependencies: Sequence[params.Depends] | None = None,
        tags: list[str | Enum] | None = None,
    ):
        def decorator(func):
            lambda_ = self.add_endpoint(
                path,
                func,
                type_="endpoint",
                response_model=response_model,
                status_code=status_code,
                method="GET",
                cache_parameters=cache_parameters,
                memory_size=memory_size,
                timeout=timeout,
                response_class=response_class,
                response_model_include=response_model_include,
                response_model_exclude=response_model_exclude,
                response_model_by_alias=response_model_by_alias,
                response_model_exclude_unset=response_model_exclude_unset,
                response_model_exclude_defaults=response_model_exclude_defaults,
                response_model_exclude_none=response_model_exclude_none,
                dependencies=dependencies,
                tags=tags,
            )
            return lambda_

        return decorator

    def post(
        self,
        path: str,
        *,
        response_model: Any = Default(None),
        status_code: int | None = None,
        cache_parameters: params.Cache | None = None,
        memory_size: int = 128,
        timeout: int = 30,
        response_class: type[ResponseAPIGateway] | DefaultPlaceholder = Default(
            ResponseAPIGateway
        ),
        response_model_include: IncEx | None = None,
        response_model_exclude: IncEx | None = None,
        response_model_by_alias: bool = True,
        response_model_exclude_unset: bool = False,
        response_model_exclude_defaults: bool = False,
        response_model_exclude_none: bool = False,
        dependencies: Sequence[params.Depends] | None = None,
        tags: list[str | Enum] | None = None,
    ):
        def decorator(func):
            lambda_ = self.add_endpoint(
                path,
                func,
                type_="endpoint",
                response_model=response_model,
                status_code=status_code,
                method="POST",
                cache_parameters=cache_parameters,
                memory_size=memory_size,
                timeout=timeout,
                response_class=response_class,
                response_model_include=response_model_include,
                response_model_exclude=response_model_exclude,
                response_model_by_alias=response_model_by_alias,
                response_model_exclude_unset=response_model_exclude_unset,
                response_model_exclude_defaults=response_model_exclude_defaults,
                response_model_exclude_none=response_model_exclude_none,
                dependencies=dependencies,
                tags=tags,
            )
            return lambda_

        return decorator

    def put(
        self,
        path: str,
        *,
        response_model: Any = Default(None),
        status_code: int | None = None,
        cache_parameters: params.Cache | None = None,
        memory_size: int = 128,
        timeout: int = 30,
        response_class: type[ResponseAPIGateway] | DefaultPlaceholder = Default(
            ResponseAPIGateway
        ),
        response_model_include: IncEx | None = None,
        response_model_exclude: IncEx | None = None,
        response_model_by_alias: bool = True,
        response_model_exclude_unset: bool = False,
        response_model_exclude_defaults: bool = False,
        response_model_exclude_none: bool = False,
        dependencies: Sequence[params.Depends] | None = None,
        tags: list[str | Enum] | None = None,
    ):
        def decorator(func):
            lambda_ = self.add_endpoint(
                path,
                func,
                type_="endpoint",
                response_model=response_model,
                status_code=status_code,
                method="PUT",
                cache_parameters=cache_parameters,
                memory_size=memory_size,
                timeout=timeout,
                response_class=response_class,
                response_model_include=response_model_include,
                response_model_exclude=response_model_exclude,
                response_model_by_alias=response_model_by_alias,
                response_model_exclude_unset=response_model_exclude_unset,
                response_model_exclude_defaults=response_model_exclude_defaults,
                response_model_exclude_none=response_model_exclude_none,
                dependencies=dependencies,
                tags=tags,
            )
            return lambda_

        return decorator

    def patch(
        self,
        path: str,
        *,
        response_model: Any = Default(None),
        status_code: int | None = None,
        cache_parameters: params.Cache | None = None,
        memory_size: int = 128,
        timeout: int = 30,
        response_class: type[ResponseAPIGateway] | DefaultPlaceholder = Default(
            ResponseAPIGateway
        ),
        response_model_include: IncEx | None = None,
        response_model_exclude: IncEx | None = None,
        response_model_by_alias: bool = True,
        response_model_exclude_unset: bool = False,
        response_model_exclude_defaults: bool = False,
        response_model_exclude_none: bool = False,
        dependencies: Sequence[params.Depends] | None = None,
        tags: list[str | Enum] | None = None,
    ):
        def decorator(func):
            lambda_ = self.add_endpoint(
                path,
                func,
                type_="endpoint",
                response_model=response_model,
                status_code=status_code,
                method="PATCH",
                cache_parameters=cache_parameters,
                memory_size=memory_size,
                timeout=timeout,
                response_class=response_class,
                response_model_include=response_model_include,
                response_model_exclude=response_model_exclude,
                response_model_by_alias=response_model_by_alias,
                response_model_exclude_unset=response_model_exclude_unset,
                response_model_exclude_defaults=response_model_exclude_defaults,
                response_model_exclude_none=response_model_exclude_none,
                dependencies=dependencies,
                tags=tags,
            )
            return lambda_

        return decorator

    def delete(
        self,
        path: str,
        *,
        response_model: Any = Default(None),
        status_code: int | None = None,
        cache_parameters: params.Cache | None = None,
        memory_size: int = 128,
        timeout: int = 30,
        response_class: type[ResponseAPIGateway] | DefaultPlaceholder = Default(
            ResponseAPIGateway
        ),
        response_model_include: IncEx | None = None,
        response_model_exclude: IncEx | None = None,
        response_model_by_alias: bool = True,
        response_model_exclude_unset: bool = False,
        response_model_exclude_defaults: bool = False,
        response_model_exclude_none: bool = False,
        dependencies: Sequence[params.Depends] | None = None,
        tags: list[str | Enum] | None = None,
    ):
        def decorator(func):
            lambda_ = self.add_endpoint(
                path,
                func,
                type_="endpoint",
                response_model=response_model,
                status_code=status_code,
                method="DELETE",
                cache_parameters=cache_parameters,
                memory_size=memory_size,
                timeout=timeout,
                response_class=response_class,
                response_model_include=response_model_include,
                response_model_exclude=response_model_exclude,
                response_model_by_alias=response_model_by_alias,
                response_model_exclude_unset=response_model_exclude_unset,
                response_model_exclude_defaults=response_model_exclude_defaults,
                response_model_exclude_none=response_model_exclude_none,
                dependencies=dependencies,
                tags=tags,
            )
            return lambda_

        return decorator

    def sqs(
        self,
        queue_name: str,
        *,
        memory_size: int = 128,
        timeout: int = 30,
        dependencies: Sequence[params.Depends] | None = None,
    ):
        def decorator(func):
            lambda_ = self.add_endpoint(
                queue_name,
                func,
                type_="queue",
                memory_size=memory_size,
                timeout=timeout,
                dependencies=dependencies,
            )
            return lambda_

        return decorator

    def job(
        self,
        expression: str,
        *,
        name: str | None = None,
        memory_size: int = 128,
        timeout: int = 30,
        dependencies: Sequence[params.Depends] | None = None,
    ):
        def decorator(func):
            lambda_ = self.add_endpoint(
                expression,
                func,
                type_="job",
                name=name,
                memory_size=memory_size,
                timeout=timeout,
                dependencies=dependencies,
            )
            return lambda_

        return decorator
