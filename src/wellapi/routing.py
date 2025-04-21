import json
import re
from collections.abc import Callable
from typing import Any

from pydantic.main import IncEx

from wellapi.convertors import CONVERTOR_TYPES
from wellapi.datastructures import Default, DefaultPlaceholder
from wellapi.dependencies.models import Dependant, ModelField
from wellapi.dependencies.utils import solve_dependencies
from wellapi.exceptions import (
    HTTPException,
    RequestValidationError,
    ResponseValidationError,
    WellAPIError,
)
from wellapi.models import RequestAPIGateway, RequestJob, RequestSQS, ResponseAPIGateway

# Match parameters in URL paths, eg. '{param}', and '{param:int}'
PARAM_REGEX = re.compile("{([a-zA-Z_][a-zA-Z0-9_]*)(:[a-zA-Z_][a-zA-Z0-9_]*)?}")


def compile_path(path: str) -> tuple:
    """
    Given a path string, like: "/{username:str}",
    or a host string, like: "{subdomain}.mydomain.org", return a three-tuple
    of (regex, format, {param_name:convertor}).

    regex:      "/(?P<username>[^/]+)"
    format:     "/{username}"
    convertors: {"username": StringConvertor()}
    """
    is_host = not path.startswith("/")

    path_regex = "^"
    path_format = ""
    duplicated_params = set()

    idx = 0
    param_convertors = {}
    for match in PARAM_REGEX.finditer(path):
        param_name, convertor_type = match.groups("str")
        convertor_type = convertor_type.lstrip(":")
        assert convertor_type in CONVERTOR_TYPES, (
            f"Unknown path convertor '{convertor_type}'"
        )
        convertor = CONVERTOR_TYPES[convertor_type]

        path_regex += re.escape(path[idx : match.start()])
        path_regex += f"(?P<{param_name}>{convertor.regex})"

        path_format += path[idx : match.start()]
        path_format += "{%s}" % param_name  # noqa: UP031

        if param_name in param_convertors:
            duplicated_params.add(param_name)

        param_convertors[param_name] = convertor

        idx = match.end()

    if duplicated_params:
        names = ", ".join(sorted(duplicated_params))
        ending = "s" if len(duplicated_params) > 1 else ""
        raise ValueError(f"Duplicated param name{ending} {names} at path {path}")

    if is_host:
        # Align with `Host.matches()` behavior, which ignores port.
        hostname = path[idx:].split(":")[0]
        path_regex += re.escape(hostname) + "$"
    else:
        path_regex += re.escape(path[idx:]) + "$"

    path_format += path[idx:]

    return re.compile(path_regex), path_format, param_convertors


def get_request_handler(
    dependant: Dependant,
    status_code: int | None = None,
    response_class: type[ResponseAPIGateway] | DefaultPlaceholder = Default(
        ResponseAPIGateway
    ),
    response_field: ModelField | None = None,
    response_model_include: IncEx | None = None,
    response_model_exclude: IncEx | None = None,
    response_model_by_alias: bool = True,
    response_model_exclude_unset: bool = False,
    response_model_exclude_defaults: bool = False,
    response_model_exclude_none: bool = False,
    embed_body_fields: bool = False,
) -> Callable[[RequestAPIGateway], ResponseAPIGateway]:
    assert dependant.call is not None, "dependant.call must be a function"

    if isinstance(response_class, DefaultPlaceholder):
        actual_response_class: type[ResponseAPIGateway] = response_class.value
    else:
        actual_response_class = response_class

    def app(request: RequestAPIGateway) -> ResponseAPIGateway:
        response: ResponseAPIGateway | None = None
        try:
            body: Any = request.json()
        except json.JSONDecodeError as e:
            validation_error = RequestValidationError(
                [
                    {
                        "type": "json_invalid",
                        "loc": ("body", e.pos),
                        "msg": "JSON decode error",
                        "input": {},
                        "ctx": {"error": e.msg},
                    }
                ],
                body=e.doc,
            )
            raise validation_error from e
        except HTTPException:
            # If a middleware raises an HTTPException, it should be raised again
            raise
        except Exception as e:
            http_error = HTTPException(
                status_code=400, detail="There was an error parsing the body"
            )
            raise http_error from e
        errors: list[Any] = []
        solved_result = solve_dependencies(
            request=request,
            dependant=dependant,
            body=body,
            embed_body_fields=embed_body_fields,
        )
        errors = solved_result.errors
        if not errors:
            raw_response = dependant.call(**solved_result.values)
            if isinstance(raw_response, ResponseAPIGateway):
                response = raw_response
            else:
                response_args: dict[str, Any] = {}
                # If status_code was set, use it, otherwise use the default from the
                # response class, in the case of redirect it's 307
                current_status_code = (
                    status_code if status_code else solved_result.response.statusCode
                )
                if current_status_code is not None:
                    response_args["status_code"] = current_status_code
                if solved_result.response.statusCode:
                    response_args["status_code"] = solved_result.response.statusCode
                content = serialize_response(
                    field=response_field,
                    response_content=raw_response,
                    include=response_model_include,
                    exclude=response_model_exclude,
                    by_alias=response_model_by_alias,
                    exclude_unset=response_model_exclude_unset,
                    exclude_defaults=response_model_exclude_defaults,
                    exclude_none=response_model_exclude_none,
                )
                response = actual_response_class(content, **response_args)
                if not is_body_allowed_for_status_code(response.statusCode):
                    response.body = ""
                response.headers.raw.extend(solved_result.response.headers.raw)
        if errors:
            validation_error = RequestValidationError(errors, body=body)
            raise validation_error
        if response is None:
            raise WellAPIError(
                "No response object was returned. There's a high chance that the "
                "application code is raising an exception and a dependency with yield "
                "has a block with a bare except, or a block with except Exception, "
                "and is not raising the exception again. Read more about it in the "
                "docs: https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/#dependencies-with-yield-and-except"
            )
        return response

    return app


def serialize_response(
    *,
    field: ModelField | None = None,
    response_content: Any,
    include: IncEx | None = None,
    exclude: IncEx | None = None,
    by_alias: bool = True,
    exclude_unset: bool = False,
    exclude_defaults: bool = False,
    exclude_none: bool = False,
) -> Any:
    if field:
        errors = []
        value, errors_ = field.validate(response_content, {}, loc=("response",))

        if isinstance(errors_, list):
            errors.extend(errors_)
        elif errors_:
            errors.append(errors_)
        if errors:
            raise ResponseValidationError(errors=errors, body=response_content)

        return field.serialize(
            value,
            include=include,
            exclude=exclude,
            by_alias=by_alias,
            exclude_unset=exclude_unset,
            exclude_defaults=exclude_defaults,
            exclude_none=exclude_none,
        )
    else:
        return json.dumps(response_content)


def is_body_allowed_for_status_code(status_code: int | str | None) -> bool:
    if status_code is None:
        return True
    # Ref: https://github.com/OAI/OpenAPI-Specification/blob/main/versions/3.1.0.md#patterned-fields-1
    if status_code in {
        "default",
        "1XX",
        "2XX",
        "3XX",
        "4XX",
        "5XX",
    }:
        return True
    current_status_code = int(status_code)
    return not (current_status_code < 200 or current_status_code in {204, 205, 304})


def request_response(
    func: Callable[[RequestAPIGateway], ResponseAPIGateway],
) -> Callable[[dict, dict], dict]:
    def app(event: dict, context: dict) -> dict:
        if "Records" in event:
            request = RequestSQS.create_request_from_event(event)
        elif "source" in event and event["source"] == "aws.events":
            request = RequestJob.create_request_from_event(event)
        else:
            request = RequestAPIGateway.create_request_from_event(event)

        resp = func(request)

        return resp.to_aws_response()

    return app
