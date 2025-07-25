import http.client
import inspect
import warnings
from collections.abc import Sequence
from typing import Any, Literal, cast

from pydantic import BaseModel
from pydantic._internal._utils import lenient_issubclass
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaValue

from wellapi.applications import Lambda
from wellapi.datastructures import DefaultPlaceholder
from wellapi.dependencies.models import Dependant, ModelField
from wellapi.dependencies.utils import (
    _get_flat_fields_from_params,
    get_flat_dependant,
    get_flat_params,
)
from wellapi.models import ResponseAPIGateway
from wellapi.openapi.models import (
    METHODS_WITH_BODY,
    REF_PREFIX,
    REF_TEMPLATE,
    OpenAPI,
    ParameterInType,
    RequestValidators,
)
from wellapi.params import Body, ParamTypes
from wellapi.routing import is_body_allowed_for_status_code

validation_error_definition = {
    "title": "ValidationError",
    "type": "object",
    "properties": {
        "loc": {
            "title": "Location",
            "type": "array",
            "items": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
        },
        "msg": {"title": "Message", "type": "string"},
        "type": {"title": "Error Type", "type": "string"},
    },
    "required": ["loc", "msg", "type"],
}

validation_error_response_definition = {
    "title": "HTTPValidationError",
    "type": "object",
    "properties": {
        "detail": {
            "title": "Detail",
            "type": "array",
            "items": {"$ref": REF_PREFIX + "ValidationError"},
        }
    },
}

status_code_ranges: dict[str, str] = {
    "1XX": "Information",
    "2XX": "Success",
    "3XX": "Redirection",
    "4XX": "Client Error",
    "5XX": "Server Error",
    "DEFAULT": "Default Response",
}

request_validators = {
    RequestValidators.basic: {
        "validateRequestBody": True,
        "validateRequestParameters": True,
    },
    RequestValidators.paramsOnly: {
        "validateRequestBody": False,
        "validateRequestParameters": True,
    },
    RequestValidators.bodyOnly: {
        "validateRequestBody": True,
        "validateRequestParameters": False,
    },
}

cors_gateway_responses = {
    "DEFAULT_4XX": {
        "responseParameters": {
            "gatewayresponse.header.Access-Control-Allow-Origin": "'*'",
            "gatewayresponse.header.Access-Control-Allow-Methods": "'*'",
            "gatewayresponse.header.Access-Control-Allow-Headers": "'*'",
        },
    }
}

cors_operation = {
    "tags": ["CORS"],
    "summary": "Mock integration for support CORS",
    "responses": {
        "200": {
            "description": "Default response",
            "headers": {
                "Access-Control-Allow-Origin": {
                    "schema": {
                        "type": "string",
                    }
                },
                "Access-Control-Allow-Methods": {
                    "schema": {
                        "type": "string",
                    }
                },
                "Access-Control-Allow-Headers": {
                    "schema": {
                        "type": "string",
                    }
                },
            },
        }
    },
    "x-amazon-apigateway-integration": {
        "type": "mock",
        "requestTemplates": {"application/json": '{"statusCode" : 200}'},
        "responses": {
            "default": {
                "statusCode": "200",
                "responseParameters": {
                    "method.response.header.Access-Control-Allow-Origin": "'*'",
                    "method.response.header.Access-Control-Allow-Methods": "'*'",
                    "method.response.header.Access-Control-Allow-Headers": "'*'",
                },
                "responseTemplates": {"application/json": "{}"},
            }
        },
    },
}


def get_openapi(
    *,
    title: str,
    version: str,
    openapi_version: str = "3.0.1",
    description: str | None = None,
    lambdas: Sequence[Lambda],
    tags: list[dict[str, Any]] | None = None,
    servers: list[dict[str, str | Any]] | None = None,
    terms_of_service: str | None = None,
    contact: dict[str, str | Any] | None = None,
    license_info: dict[str, str | Any] | None = None,
    separate_input_output_schemas: bool = True,
    cors: bool = False,
    role_name: str = "WellApiRole",
) -> dict[str, Any]:
    info: dict[str, Any] = {"title": title, "version": version}
    if description:
        info["description"] = description
    if terms_of_service:
        info["termsOfService"] = terms_of_service
    if contact:
        info["contact"] = contact
    if license_info:
        info["license"] = license_info
    output: dict[str, Any] = {
        "openapi": openapi_version,
        "info": info,
        "x-amazon-apigateway-request-validators": request_validators,
    }
    if servers:
        output["servers"] = servers
    if cors:
        output["x-amazon-apigateway-gateway-responses"] = cors_gateway_responses
    components: dict[str, dict[str, Any]] = {}
    paths: dict[str, dict[str, Any]] = {}
    webhook_paths: dict[str, dict[str, Any]] = {}
    operation_ids: set[str] = set()
    all_fields = get_fields_from_routes(list(lambdas or []))
    field_mapping, definitions = get_definitions(
        fields=all_fields,
        separate_input_output_schemas=separate_input_output_schemas,
    )
    for lambda_ in lambdas or []:
        if lambda_.type_ != "endpoint":
            continue

        result = get_openapi_path(
            route=lambda_,
            operation_ids=operation_ids,
            field_mapping=field_mapping,
            separate_input_output_schemas=separate_input_output_schemas,
            cors=cors,
            role_name=role_name,
        )
        if result:
            path, security_schemes, path_definitions = result
            if path:
                paths.setdefault(lambda_.path_format, {}).update(path)
            if security_schemes:
                components.setdefault("securitySchemes", {}).update(security_schemes)
            if path_definitions:
                definitions.update(path_definitions)

    if definitions:
        components["schemas"] = {k: definitions[k] for k in sorted(definitions)}
    if components:
        output["components"] = components

    output["paths"] = paths
    if webhook_paths:
        output["webhooks"] = webhook_paths
    if tags:
        output["tags"] = tags
    return OpenAPI(**output).model_dump(by_alias=True, exclude_none=True, mode="json")


def get_fields_from_routes(
    routes: Sequence[Lambda],
) -> list[ModelField]:
    body_fields_from_routes: list[ModelField] = []
    responses_from_routes: list[ModelField] = []
    request_fields_from_routes: list[ModelField] = []
    callback_flat_models: list[ModelField] = []
    for route in routes:
        if route.type_ != "endpoint":
            continue

        if route.body_field:
            assert isinstance(route.body_field, ModelField), (
                "A request body must be a Pydantic Field"
            )
            body_fields_from_routes.append(route.body_field)
        if route.response_field:
            responses_from_routes.append(route.response_field)
        if route.response_fields:
            responses_from_routes.extend(route.response_fields.values())

        params = get_flat_params(route.dependant)
        request_fields_from_routes.extend(params)

    flat_models = callback_flat_models + list(
        body_fields_from_routes + responses_from_routes + request_fields_from_routes
    )
    return flat_models


def get_definitions(
    *,
    fields: list[ModelField],
    separate_input_output_schemas: bool = True,
) -> tuple[
    dict[tuple[ModelField, Literal["validation", "serialization"]], JsonSchemaValue],
    dict[str, dict[str, Any]],
]:
    override_mode: Literal["validation"] | None = (
        None if separate_input_output_schemas else "validation"
    )
    inputs = [
        (field, override_mode or field.mode, field._type_adapter.core_schema)
        for field in fields
    ]
    schema_generator = GenerateJsonSchema(ref_template=REF_TEMPLATE)
    field_mapping, definitions = schema_generator.generate_definitions(inputs=inputs)
    return field_mapping, definitions


def get_openapi_path(
    *,
    route: Lambda,
    operation_ids: set[str],
    field_mapping: dict[
        tuple[ModelField, Literal["validation", "serialization"]], JsonSchemaValue
    ],
    role_name: str,
    separate_input_output_schemas: bool = True,
    cors: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    path = {}
    security_schemes: dict[str, Any] = {}
    definitions: dict[str, Any] = {}
    if isinstance(route.response_class, DefaultPlaceholder):
        current_response_class: type[ResponseAPIGateway] = route.response_class.value
    else:
        current_response_class = route.response_class
    assert current_response_class, "A response class is needed to generate OpenAPI"
    route_response_media_type = "application/json"
    operation = get_openapi_operation_metadata(
        route=route, operation_ids=operation_ids, role_name=role_name
    )
    parameters: list[dict[str, Any]] = []
    flat_dependant = get_flat_dependant(route.dependant, skip_repeats=True)
    security_definitions, operation_security = get_openapi_security_definitions(
        flat_dependant=flat_dependant
    )
    if operation_security:
        operation.setdefault("security", []).extend(operation_security)
    if security_definitions:
        security_schemes.update(security_definitions)
    operation_parameters = _get_openapi_operation_parameters(
        dependant=route.dependant,
        field_mapping=field_mapping,
        separate_input_output_schemas=separate_input_output_schemas,
    )

    if operation_parameters and route.cache_parameters:
        cache_key_parameters = []
        for cache_param, in_, location in route.cache_parameters:
            for param in operation_parameters:
                if param["name"] == cache_param and param["in"] == in_:
                    cache_key_parameters.append(
                        f"method.request.{location}.{cache_param}"
                    )
                    break
            else:
                raise ValueError(
                    f"Cache parameter '{cache_param}' not found in request parameters"
                )

        operation["x-amazon-apigateway-integration"].update(
            {
                "cacheKeyParameters": cache_key_parameters,
                "cacheNamespace": route.unique_id,
            }
        )

    parameters.extend(operation_parameters)
    if parameters:
        all_parameters = {(param["in"], param["name"]): param for param in parameters}
        required_parameters = {
            (param["in"], param["name"]): param
            for param in parameters
            if param.get("required")
        }
        # Make sure required definitions of the same parameter take precedence
        # over non-required definitions
        all_parameters.update(required_parameters)
        operation["parameters"] = list(all_parameters.values())
    if route.method in METHODS_WITH_BODY:
        request_body_oai = get_openapi_operation_request_body(
            body_field=route.body_field,
            field_mapping=field_mapping,
            separate_input_output_schemas=separate_input_output_schemas,
        )
        if request_body_oai:
            operation["requestBody"] = request_body_oai

    request_validator: RequestValidators | None = None
    if "parameters" in operation and "requestBody" in operation:
        request_validator = RequestValidators.basic
    elif "parameters" in operation:
        request_validator = RequestValidators.paramsOnly
    elif "requestBody" in operation:
        request_validator = RequestValidators.bodyOnly
    operation["x-amazon-apigateway-request-validator"] = request_validator

    if route.status_code is not None:
        status_code = str(route.status_code)
    else:
        # It would probably make more sense for all response classes to have an
        # explicit default status_code, and to extract it from them, instead of
        # doing this inspection tricks, that would probably be in the future
        # TODO: probably make status_code a default class attribute for all
        # responses in Starlette
        response_signature = inspect.signature(current_response_class.__init__)
        status_code_param = response_signature.parameters.get("status_code")
        if status_code_param is not None:
            if isinstance(status_code_param.default, int):
                status_code = str(status_code_param.default)
    operation.setdefault("responses", {}).setdefault(status_code, {})["description"] = (
        route.response_description
    )
    if route_response_media_type and is_body_allowed_for_status_code(route.status_code):
        if route.response_field:
            response_schema = get_schema_from_model_field(
                field=route.response_field,
                field_mapping=field_mapping,
                separate_input_output_schemas=separate_input_output_schemas,
            )
        else:
            response_schema = {}
        operation.setdefault("responses", {}).setdefault(status_code, {}).setdefault(
            "content", {}
        ).setdefault(route_response_media_type, {})["schema"] = response_schema
    if route.responses:
        operation_responses = operation.setdefault("responses", {})
        for (
            additional_status_code,
            additional_response,
        ) in route.responses.items():
            process_response = additional_response.copy()
            process_response.pop("model", None)
            status_code_key = str(additional_status_code).upper()
            if status_code_key == "DEFAULT":
                status_code_key = "default"
            openapi_response = operation_responses.setdefault(status_code_key, {})
            assert isinstance(process_response, dict), (
                "An additional response must be a dict"
            )
            field = route.response_fields.get(additional_status_code)
            additional_field_schema: dict[str, Any] | None = None
            if field:
                additional_field_schema = get_schema_from_model_field(
                    field=field,
                    field_mapping=field_mapping,
                    separate_input_output_schemas=separate_input_output_schemas,
                )
                media_type = route_response_media_type
                additional_schema = (
                    process_response.setdefault("content", {})
                    .setdefault(media_type, {})
                    .setdefault("schema", {})
                )
                deep_dict_update(additional_schema, additional_field_schema)
            status_text: str | None = status_code_ranges.get(
                str(additional_status_code).upper()
            ) or http.client.responses.get(int(additional_status_code))
            description = (
                process_response.get("description")
                or openapi_response.get("description")
                or status_text
                or "Additional Response"
            )
            deep_dict_update(openapi_response, process_response)
            openapi_response["description"] = description
    http422 = "422"
    all_route_params = get_flat_params(route.dependant)
    if (all_route_params or route.body_field) and not any(
        status in operation["responses"] for status in [http422, "4XX", "default"]
    ):
        operation["responses"][http422] = {
            "description": "Validation Error",
            "content": {
                "application/json": {
                    "schema": {"$ref": REF_PREFIX + "HTTPValidationError"}
                }
            },
        }
        if "ValidationError" not in definitions:
            definitions.update(
                {
                    "ValidationError": validation_error_definition,
                    "HTTPValidationError": validation_error_response_definition,
                }
            )

    if cors:
        path["options"] = cors_operation

    path[route.method.lower()] = operation
    return path, security_schemes, definitions


def get_openapi_operation_metadata(
    *, route: Lambda, operation_ids: set[str], role_name: str
) -> dict[str, Any]:
    operation: dict[str, Any] = {}
    if route.tags:
        operation["tags"] = route.tags
    operation["summary"] = route.summary or route.name.replace("_", " ").title()
    if route.description:
        operation["description"] = route.description
    operation_id = route.operation_id or route.unique_id
    if operation_id in operation_ids:
        message = (
            f"Duplicate Operation ID {operation_id} for function "
            + f"{route.endpoint.__name__}"
        )
        file_name = getattr(route.endpoint, "__globals__", {}).get("__file__")
        if file_name:
            message += f" at {file_name}"
        warnings.warn(message, stacklevel=1)
    operation_ids.add(operation_id)
    operation["operationId"] = operation_id
    if route.deprecated:
        operation["deprecated"] = route.deprecated

    operation["x-amazon-apigateway-integration"] = {
        "uri": {
            "Fn::Sub": f"arn:aws:apigateway:${{AWS::Region}}:lambda:path/2015-03-31/functions/${{{route.arn}Function.Arn}}/invocations"
        },
        "passthroughBehavior": "when_no_match",
        "httpMethod": "POST",
        "type": "aws_proxy",
        "credentials": {
            "Fn::Sub": f"arn:aws:iam::${{AWS::AccountId}}:role/${{{role_name}}}"
        },
    }

    return operation


def get_openapi_security_definitions(
    flat_dependant: Dependant,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    security_definitions = {}
    operation_security = []
    for security_requirement in flat_dependant.security_requirements:
        security_definition = security_requirement.security_scheme.model.model_dump(
            by_alias=True,
            exclude_none=True,
        )

        security_name = security_requirement.security_scheme.scheme_name
        security_definitions[security_name] = security_definition
        operation_security.append({security_name: security_requirement.scopes})
    return security_definitions, operation_security


def get_schema_from_model_field(
    *,
    field: ModelField,
    field_mapping: dict[
        tuple[ModelField, Literal["validation", "serialization"]], JsonSchemaValue
    ],
    separate_input_output_schemas: bool = True,
) -> dict[str, Any]:
    override_mode: Literal["validation"] | None = (
        None if separate_input_output_schemas else "validation"
    )
    # This expects that GenerateJsonSchema was already used to generate the definitions
    json_schema = field_mapping[(field, override_mode or field.mode)]

    return json_schema


def _get_openapi_operation_parameters(
    *,
    dependant: Dependant,
    field_mapping: dict[
        tuple[ModelField, Literal["validation", "serialization"]], JsonSchemaValue
    ],
    separate_input_output_schemas: bool = True,
) -> list[dict[str, Any]]:
    parameters = []
    flat_dependant = get_flat_dependant(dependant, skip_repeats=True)
    path_params = _get_flat_fields_from_params(flat_dependant.path_params)
    query_params = _get_flat_fields_from_params(flat_dependant.query_params)
    header_params = _get_flat_fields_from_params(flat_dependant.header_params)
    cookie_params = _get_flat_fields_from_params(flat_dependant.cookie_params)
    parameter_groups = [
        (ParamTypes.path, path_params),
        (ParamTypes.query, query_params),
        (ParamTypes.header, header_params),
        (ParamTypes.cookie, cookie_params),
    ]
    default_convert_underscores = True
    if len(flat_dependant.header_params) == 1:
        first_field = flat_dependant.header_params[0]
        if lenient_issubclass(first_field.type_, BaseModel):
            default_convert_underscores = getattr(
                first_field.field_info, "convert_underscores", True
            )
    for param_type, param_group in parameter_groups:
        for param in param_group:
            field_info = param.field_info
            # field_info = cast(Param, field_info)
            if not getattr(field_info, "include_in_schema", True):
                continue
            param_schema = get_schema_from_model_field(
                field=param,
                field_mapping=field_mapping,
                separate_input_output_schemas=separate_input_output_schemas,
            )
            name = param.alias
            convert_underscores = getattr(
                param.field_info,
                "convert_underscores",
                default_convert_underscores,
            )
            if (
                param_type == ParamTypes.header
                and param.alias == param.name
                and convert_underscores
            ):
                name = param.name.replace("_", "-")

            parameter = {
                "name": name,
                "in": ParameterInType(param_type.value),
                "required": param.required,
                "schema": param_schema,
            }
            if field_info.description:
                parameter["description"] = field_info.description

            if getattr(field_info, "deprecated", None):
                parameter["deprecated"] = True
            parameters.append(parameter)
    return parameters


def get_openapi_operation_request_body(
    *,
    body_field: ModelField | None,
    field_mapping: dict[
        tuple[ModelField, Literal["validation", "serialization"]], JsonSchemaValue
    ],
    separate_input_output_schemas: bool = True,
) -> dict[str, Any] | None:
    if not body_field:
        return None
    assert isinstance(body_field, ModelField)
    body_schema = get_schema_from_model_field(
        field=body_field,
        field_mapping=field_mapping,
        separate_input_output_schemas=separate_input_output_schemas,
    )
    field_info = cast(Body, body_field.field_info)
    request_media_type = field_info.media_type
    required = body_field.required
    request_body_oai: dict[str, Any] = {}
    if required:
        request_body_oai["required"] = required
    request_media_content: dict[str, Any] = {"schema": body_schema}

    request_body_oai["content"] = {request_media_type: request_media_content}
    return request_body_oai


def deep_dict_update(main_dict: dict[Any, Any], update_dict: dict[Any, Any]) -> None:
    for key, value in update_dict.items():
        if (
            key in main_dict
            and isinstance(main_dict[key], dict)
            and isinstance(value, dict)
        ):
            deep_dict_update(main_dict[key], value)
        elif (
            key in main_dict
            and isinstance(main_dict[key], list)
            and isinstance(update_dict[key], list)
        ):
            main_dict[key] = main_dict[key] + update_dict[key]
        else:
            main_dict[key] = value
