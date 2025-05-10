import inspect
import re
from collections import deque
from collections.abc import Mapping, Sequence
from copy import copy, deepcopy
from dataclasses import dataclass, is_dataclass
from functools import lru_cache
from types import UnionType

# ruff: noqa: UP035
from typing import (
    Annotated,
    Any,
    Callable,
    Deque,
    ForwardRef,
    FrozenSet,
    List,
    Literal,
    Set,
    Tuple,
    Union,
    cast,
)

from pydantic import (
    BaseModel,
    PydanticSchemaGenerationError,
    ValidationError,
    create_model,
)
from pydantic._internal._typing_extra import try_eval_type
from pydantic._internal._utils import lenient_issubclass
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined
from typing_extensions import get_args, get_origin

from wellapi import params
from wellapi.datastructures import Headers, ImmutableMultiDict, QueryParams
from wellapi.dependencies.models import (
    Dependant,
    ModelField,
    SecurityRequirement,
    _regenerate_error_with_loc,
)
from wellapi.exceptions import WellAPIError
from wellapi.models import RequestAPIGateway, RequestSQS, ResponseAPIGateway
from wellapi.security import OAuth2, SecurityBase


def get_dependant(
    *,
    path: str,
    call: Callable[..., Any],
    type_: Literal["endpoint", "queue", "job"],
    name: str | None = None,
    security_scopes: list[str] | None = None,
    use_cache: bool = True,
) -> Dependant:
    path_param_names = get_path_param_names(path)
    endpoint_signature = get_typed_signature(call)
    signature_params = endpoint_signature.parameters
    dependant = Dependant(
        call=call,
        name=name,
        path=path,
        security_scopes=security_scopes,
        use_cache=use_cache,
    )
    for param_name, param in signature_params.items():
        is_path_param = param_name in path_param_names
        param_details = analyze_param(
            param_name=param_name,
            annotation=param.annotation,
            value=param.default,
            is_path_param=is_path_param,
            type_=type_,
        )
        if param_details.depends is not None:
            sub_dependant = get_param_sub_dependant(
                param_name=param_name,
                depends=param_details.depends,
                path=path,
                security_scopes=security_scopes,
                type_=type_,
            )
            dependant.dependencies.append(sub_dependant)
            continue
        if add_non_field_param_to_dependency(
            param_name=param_name,
            type_annotation=param_details.type_annotation,
            dependant=dependant,
        ):
            assert param_details.field is None, (
                f"Cannot specify multiple FastAPI annotations for {param_name!r}"
            )
            continue
        assert param_details.field is not None
        if isinstance(param_details.field.field_info, params.Body):
            dependant.body_params.append(param_details.field)
        else:
            add_param_to_fields(field=param_details.field, dependant=dependant)
    return dependant


def add_param_to_fields(*, field: ModelField, dependant: Dependant) -> None:
    field_info = field.field_info
    field_info_in = getattr(field_info, "in_", None)
    if field_info_in == params.ParamTypes.path:
        dependant.path_params.append(field)
    elif field_info_in == params.ParamTypes.query:
        dependant.query_params.append(field)
    elif field_info_in == params.ParamTypes.header:
        dependant.header_params.append(field)
    else:
        assert field_info_in == params.ParamTypes.cookie, (
            f"non-body parameters must be in path, query, header or cookie: {field.name}"
        )
        dependant.cookie_params.append(field)


def add_non_field_param_to_dependency(
    *, param_name: str, type_annotation: Any, dependant: Dependant
) -> bool | None:
    if lenient_issubclass(type_annotation, RequestAPIGateway):
        dependant.request_param_name = param_name
        return True
    elif lenient_issubclass(type_annotation, ResponseAPIGateway):
        dependant.response_param_name = param_name
        return True
    elif lenient_issubclass(type_annotation, RequestSQS):
        dependant.request_sqs_param_name = param_name
        return True

    return None


def get_param_sub_dependant(
    *,
    param_name: str,
    depends: params.Depends,
    path: str,
    type_: Literal["endpoint", "queue", "job"],
    security_scopes: list[str] | None = None,
) -> Dependant:
    assert depends.dependency
    return get_sub_dependant(
        depends=depends,
        dependency=depends.dependency,
        path=path,
        type_=type_,
        name=param_name,
        security_scopes=security_scopes,
    )


def get_parameterless_sub_dependant(
    *,
    depends: params.Depends,
    path: str,
    type_: Literal["endpoint", "queue", "job"],
) -> Dependant:
    assert callable(depends.dependency), (
        "A parameter-less dependency must have a callable dependency"
    )
    return get_sub_dependant(
        depends=depends, dependency=depends.dependency, path=path, type_=type_
    )


def get_sub_dependant(
    *,
    depends: params.Depends,
    dependency: Callable[..., Any],
    path: str,
    type_: Literal["endpoint", "queue", "job"],
    name: str | None = None,
    security_scopes: list[str] | None = None,
) -> Dependant:
    security_requirement = None
    security_scopes = security_scopes or []
    if isinstance(depends, params.Security):
        dependency_scopes = depends.scopes
        security_scopes.extend(dependency_scopes)
    if isinstance(dependency, SecurityBase):
        use_scopes: list[str] = []
        if isinstance(dependency, OAuth2):
            use_scopes = security_scopes
        security_requirement = SecurityRequirement(
            security_scheme=dependency, scopes=use_scopes
        )
    sub_dependant = get_dependant(
        path=path,
        call=dependency,
        name=name,
        type_=type_,
        security_scopes=security_scopes,
        use_cache=depends.use_cache,
    )
    if security_requirement:
        sub_dependant.security_requirements.append(security_requirement)
    return sub_dependant


def get_path_param_names(path: str) -> set[str]:
    return set(re.findall("{(.*?)}", path))


def get_typed_signature(call: Callable[..., Any]) -> inspect.Signature:
    signature = inspect.signature(call)
    globalns = getattr(call, "__globals__", {})
    typed_params = [
        inspect.Parameter(
            name=param.name,
            kind=param.kind,
            default=param.default,
            annotation=get_typed_annotation(param.annotation, globalns),
        )
        for param in signature.parameters.values()
    ]
    typed_signature = inspect.Signature(typed_params)
    return typed_signature


def get_typed_annotation(annotation: Any, globalns: dict[str, Any]) -> Any:
    if isinstance(annotation, str):
        annotation = ForwardRef(annotation)
        annotation, _ = try_eval_type(annotation, globalns, globalns)
    return annotation


# ruff: noqa: UP006
sequence_annotation_to_type = {
    Sequence: list,
    List: list,
    list: list,
    Tuple: tuple,
    tuple: tuple,
    Set: set,
    set: set,
    FrozenSet: frozenset,
    frozenset: frozenset,
    Deque: deque,
    deque: deque,
}

sequence_types = tuple(sequence_annotation_to_type.keys())


def _annotation_is_sequence(annotation: type[Any] | None) -> bool:
    if lenient_issubclass(annotation, (str, bytes)):
        return False
    return lenient_issubclass(annotation, sequence_types)


def _annotation_is_complex(annotation: type[Any] | None) -> bool:
    return (
        lenient_issubclass(annotation, (BaseModel, Mapping))
        or _annotation_is_sequence(annotation)
        or is_dataclass(annotation)
    )


def field_annotation_is_complex(annotation: type[Any] | None) -> bool:
    origin = get_origin(annotation)
    if origin is Union or origin is UnionType:
        return any(field_annotation_is_complex(arg) for arg in get_args(annotation))

    return (
        _annotation_is_complex(annotation)
        or _annotation_is_complex(origin)
        or hasattr(origin, "__pydantic_core_schema__")
        or hasattr(origin, "__get_pydantic_core_schema__")
    )


def field_annotation_is_scalar(annotation: Any) -> bool:
    # handle Ellipsis here to make tuple[int, ...] work nicely
    return annotation is Ellipsis or not field_annotation_is_complex(annotation)


def create_model_field(
    name: str,
    type_: Any,
    default: Any | None = PydanticUndefined,
    field_info: FieldInfo | None = None,
    alias: str | None = None,
    mode: Literal["validation", "serialization"] = "validation",
) -> ModelField:
    field_info = field_info or FieldInfo(annotation=type_, default=default, alias=alias)

    try:
        return ModelField(
            name=name,
            field_info=field_info,
            mode=mode,
        )
    except (RuntimeError, PydanticSchemaGenerationError):
        raise WellAPIError(
            "Invalid args for response field! Hint: "
            f"check that {type_} is a valid Pydantic field type. "
            "If you are using a return type annotation that is not a valid Pydantic "
            "field (e.g. Union[Response, dict, None]) you can disable generating the "
            "response model from the type annotation with the path operation decorator "
            "parameter response_model=None. Read more: "
            "https://fastapi.tiangolo.com/tutorial/response-model/"
        ) from None


def is_scalar_field(field: ModelField) -> bool:
    return field_annotation_is_scalar(field.field_info.annotation) and not isinstance(
        field.field_info, params.Body
    )


def is_scalar_sequence_field(field: ModelField) -> bool:
    return field_annotation_is_scalar_sequence(field.field_info.annotation)


def field_annotation_is_scalar_sequence(annotation: type[Any] | None) -> bool:
    origin = get_origin(annotation)
    if origin is Union or origin is UnionType:
        at_least_one_scalar_sequence = False
        for arg in get_args(annotation):
            if field_annotation_is_scalar_sequence(arg):
                at_least_one_scalar_sequence = True
                continue
            elif not field_annotation_is_scalar(arg):
                return False
        return at_least_one_scalar_sequence
    return field_annotation_is_sequence(annotation) and all(
        field_annotation_is_scalar(sub_annotation)
        for sub_annotation in get_args(annotation)
    )


def field_annotation_is_sequence(annotation: type[Any] | None) -> bool:
    origin = get_origin(annotation)
    if origin is Union or origin is UnionType:
        for arg in get_args(annotation):
            if field_annotation_is_sequence(arg):
                return True
        return False
    return _annotation_is_sequence(annotation) or _annotation_is_sequence(
        get_origin(annotation)
    )


@dataclass
class ParamDetails:
    type_annotation: Any
    depends: params.Depends | None
    field: ModelField | None


def analyze_param(
    *,
    param_name: str,
    annotation: Any,
    value: Any,
    is_path_param: bool,
    type_: Literal["endpoint", "queue", "job"],
) -> ParamDetails:
    field_info = None
    depends = None
    type_annotation: Any = Any
    use_annotation: Any = Any
    if annotation is not inspect.Signature.empty:
        use_annotation = annotation
        type_annotation = annotation
    # Extract Annotated info
    if get_origin(use_annotation) is Annotated:
        annotated_args = get_args(annotation)
        type_annotation = annotated_args[0]
        fastapi_annotations = [
            arg
            for arg in annotated_args[1:]
            if isinstance(arg, FieldInfo | params.Depends)
        ]
        fastapi_specific_annotations = [
            arg
            for arg in fastapi_annotations
            if isinstance(arg, params.Param | params.Body | params.Depends)
        ]
        if fastapi_specific_annotations:
            fastapi_annotation: FieldInfo | params.Depends | None = (
                fastapi_specific_annotations[-1]
            )
        else:
            fastapi_annotation = None
        # Set default for Annotated FieldInfo
        if isinstance(fastapi_annotation, FieldInfo):
            # Copy `field_info` because we mutate `field_info.default` below.
            field_info = copy_field_info(
                field_info=fastapi_annotation, annotation=use_annotation
            )
            assert (
                field_info.default is PydanticUndefined
                or field_info.default is Ellipsis
            ), (
                f"`{field_info.__class__.__name__}` default value cannot be set in"
                f" `Annotated` for {param_name!r}. Set the default value with `=` instead."
            )
            if value is not inspect.Signature.empty:
                assert not is_path_param, "Path parameters cannot have default values"
                field_info.default = value
            else:
                field_info.default = PydanticUndefined
        # Get Annotated Depends
        elif isinstance(fastapi_annotation, params.Depends):
            depends = fastapi_annotation
    # Get Depends from default value
    if isinstance(value, params.Depends):
        assert depends is None, (
            "Cannot specify `Depends` in `Annotated` and default value"
            f" together for {param_name!r}"
        )
        assert field_info is None, (
            "Cannot specify a FastAPI annotation in `Annotated` and `Depends` as a"
            f" default value together for {param_name!r}"
        )
        depends = value
    # Get FieldInfo from default value
    elif isinstance(value, FieldInfo):
        assert field_info is None, (
            "Cannot specify FastAPI annotations in `Annotated` and default value"
            f" together for {param_name!r}"
        )
        field_info = value
        field_info.annotation = type_annotation

    # Get Depends from type annotation
    if depends is not None and depends.dependency is None:
        # Copy `depends` before mutating it
        depends = copy(depends)
        depends.dependency = type_annotation

    # Handle non-param type annotations like Request
    if lenient_issubclass(
        type_annotation,
        (RequestAPIGateway, ResponseAPIGateway, RequestSQS),
    ):
        assert depends is None, f"Cannot specify `Depends` for type {type_annotation!r}"
        assert field_info is None, (
            f"Cannot specify FastAPI annotation for type {type_annotation!r}"
        )
    # Handle default assignations, neither field_info nor depends was not found in Annotated nor default value
    elif field_info is None and depends is None:
        default_value = value if value is not inspect.Signature.empty else Ellipsis
        if is_path_param:
            # We might check here that `default_value is RequiredParam`, but the fact is that the same
            # parameter might sometimes be a path parameter and sometimes not. See
            # `tests/test_infer_param_optionality.py` for an example.
            field_info = params.Path(annotation=use_annotation)
        elif not field_annotation_is_scalar(annotation=type_annotation):
            field_info = params.Body(annotation=use_annotation, default=default_value)
        else:
            field_info = params.Query(annotation=use_annotation, default=default_value)

    field = None
    # It's a field_info, not a dependency
    if field_info is not None:
        if type_ in ("queue", "job"):
            assert not isinstance(field_info, params.Param), (
                f"Cannot use `{field_info.__class__.__name__}` for {type_} param"
            )
        if type_ == "job":
            assert isinstance(field_info, params.Body), (
                f"Cannot use `{field_info.__class__.__name__}` for job param"
            )
        # Handle field_info.in_
        if is_path_param:
            assert isinstance(field_info, params.Path), (
                f"Cannot use `{field_info.__class__.__name__}` for path param"
                f" {param_name!r}"
            )
        elif (
            isinstance(field_info, params.Param)
            and getattr(field_info, "in_", None) is None
        ):
            field_info.in_ = params.ParamTypes.query

        if not field_info.alias and getattr(field_info, "convert_underscores", None):
            alias = param_name.replace("_", "-")
        else:
            alias = field_info.alias or param_name
        field_info.alias = alias
        field = create_model_field(
            name=param_name,
            type_=use_annotation,
            default=field_info.default,
            alias=alias,
            field_info=field_info,
        )
        if is_path_param:
            assert is_scalar_field(field=field), (
                "Path params must be of one of the supported types"
            )
        elif isinstance(field_info, params.Query):
            assert (
                is_scalar_field(field)
                or is_scalar_sequence_field(field)
                or lenient_issubclass(field.type_, BaseModel)
            )

    return ParamDetails(type_annotation=type_annotation, depends=depends, field=field)


def copy_field_info(*, field_info: FieldInfo, annotation: Any) -> FieldInfo:
    cls = type(field_info)
    merged_field_info = cls.from_annotation(annotation)
    new_field_info = copy(field_info)
    new_field_info.metadata = merged_field_info.metadata
    new_field_info.annotation = merged_field_info.annotation
    return new_field_info


CacheKey = tuple[Callable[..., Any] | None, tuple[str, ...]]


def get_flat_dependant(
    dependant: Dependant,
    *,
    skip_repeats: bool = False,
    visited: list[CacheKey] | None = None,
) -> Dependant:
    if visited is None:
        visited = []
    visited.append(dependant.cache_key)

    flat_dependant = Dependant(
        path_params=dependant.path_params.copy(),
        query_params=dependant.query_params.copy(),
        header_params=dependant.header_params.copy(),
        cookie_params=dependant.cookie_params.copy(),
        body_params=dependant.body_params.copy(),
        security_requirements=dependant.security_requirements.copy(),
        use_cache=dependant.use_cache,
        path=dependant.path,
    )
    for sub_dependant in dependant.dependencies:
        if skip_repeats and sub_dependant.cache_key in visited:
            continue
        flat_sub = get_flat_dependant(
            sub_dependant, skip_repeats=skip_repeats, visited=visited
        )
        flat_dependant.path_params.extend(flat_sub.path_params)
        flat_dependant.query_params.extend(flat_sub.query_params)
        flat_dependant.header_params.extend(flat_sub.header_params)
        flat_dependant.cookie_params.extend(flat_sub.cookie_params)
        flat_dependant.body_params.extend(flat_sub.body_params)
        flat_dependant.security_requirements.extend(flat_sub.security_requirements)
    return flat_dependant


def _should_embed_body_fields(fields: list[ModelField]) -> bool:
    if not fields:
        return False
    # More than one dependency could have the same field, it would show up as multiple
    # fields but it's the same one, so count them by name
    body_param_names_set = {field.name for field in fields}
    # A top level field has to be a single field, not multiple
    if len(body_param_names_set) > 1:
        return True
    first_field = fields[0]
    # If it explicitly specifies it is embedded, it has to be embedded
    if getattr(first_field.field_info, "embed", None):
        return True

    return False


def get_body_field(
    *, flat_dependant: Dependant, name: str, embed_body_fields: bool
) -> ModelField | None:
    """
    Get a ModelField representing the request body for a path operation, combining
    all body parameters into a single field if necessary.

    Used to check if it's form data (with `isinstance(body_field, params.Form)`)
    or JSON and to generate the JSON Schema for a request body.

    This is **not** used to validate/parse the request body, that's done with each
    individual body parameter.
    """
    if not flat_dependant.body_params:
        return None
    first_param = flat_dependant.body_params[0]
    if not embed_body_fields:
        return first_param
    model_name = "Body" + name
    BodyModel = create_body_model(
        fields=flat_dependant.body_params, model_name=model_name
    )
    required = any(True for f in flat_dependant.body_params if f.required)
    BodyFieldInfo_kwargs: dict[str, Any] = {
        "annotation": BodyModel,
        "alias": "body",
    }
    if not required:
        BodyFieldInfo_kwargs["default"] = None

    body_param_media_types = [
        f.field_info.media_type
        for f in flat_dependant.body_params
        if isinstance(f.field_info, params.Body)
    ]
    if len(set(body_param_media_types)) == 1:
        BodyFieldInfo_kwargs["media_type"] = body_param_media_types[0]

    final_field = create_model_field(
        name="body",
        type_=BodyModel,
        alias="body",
        field_info=params.Body(**BodyFieldInfo_kwargs),
    )
    return final_field


def create_body_model(
    *, fields: Sequence[ModelField], model_name: str
) -> type[BaseModel]:
    field_params = {f.name: (f.field_info.annotation, f.field_info) for f in fields}
    BodyModel: type[BaseModel] = create_model(model_name, **field_params)
    return BodyModel


@dataclass
class SolvedDependency:
    values: dict[str, Any]
    errors: list[Any]
    response: ResponseAPIGateway
    dependency_cache: dict[tuple[Callable[..., Any], tuple[str]], Any]


def solve_dependencies(
    *,
    request: RequestAPIGateway,
    dependant: Dependant,
    body: dict[str, Any] | None = None,
    response: ResponseAPIGateway | None = None,
    dependency_cache: dict[tuple[Callable[..., Any], tuple[str]], Any] | None = None,
    embed_body_fields: bool,
) -> SolvedDependency:
    values: dict[str, Any] = {}
    errors: list[Any] = []
    if response is None:
        response = ResponseAPIGateway()
        del response.headers["content-length"]
        response.statusCode = None  # type: ignore
    dependency_cache = dependency_cache or {}
    sub_dependant: Dependant
    for sub_dependant in dependant.dependencies:
        sub_dependant.call = cast(Callable[..., Any], sub_dependant.call)
        sub_dependant.cache_key = cast(
            tuple[Callable[..., Any], tuple[str]], sub_dependant.cache_key
        )
        call = sub_dependant.call
        use_sub_dependant = sub_dependant

        solved_result = solve_dependencies(
            request=request,
            dependant=use_sub_dependant,
            body=body,
            response=response,
            dependency_cache=dependency_cache,
            embed_body_fields=embed_body_fields,
        )
        dependency_cache.update(solved_result.dependency_cache)
        if solved_result.errors:
            errors.extend(solved_result.errors)
            continue
        if sub_dependant.use_cache and sub_dependant.cache_key in dependency_cache:
            solved = dependency_cache[sub_dependant.cache_key]
        else:
            solved = call(**solved_result.values)
        if sub_dependant.name is not None:
            values[sub_dependant.name] = solved
        if sub_dependant.cache_key not in dependency_cache:
            dependency_cache[sub_dependant.cache_key] = solved
    path_values, path_errors = request_params_to_args(
        dependant.path_params, request.path_params
    )
    query_values, query_errors = request_params_to_args(
        dependant.query_params, request.query_params
    )
    header_values, header_errors = request_params_to_args(
        dependant.header_params, request.headers
    )
    cookie_values, cookie_errors = request_params_to_args(
        dependant.cookie_params, request.cookies
    )
    values.update(path_values)
    values.update(query_values)
    values.update(header_values)
    values.update(cookie_values)
    errors += path_errors + query_errors + header_errors + cookie_errors
    if dependant.body_params:
        (
            body_values,
            body_errors,
        ) = request_body_to_args(  # body_params checked above
            body_fields=dependant.body_params,
            received_body=body,
            embed_body_fields=embed_body_fields,
        )
        values.update(body_values)
        errors.extend(body_errors)
    if dependant.http_connection_param_name:
        values[dependant.http_connection_param_name] = request
    if dependant.request_param_name and isinstance(request, RequestAPIGateway):
        values[dependant.request_param_name] = request
    if dependant.request_sqs_param_name and isinstance(request, RequestSQS):
        values[dependant.request_sqs_param_name] = request
    if dependant.response_param_name:
        values[dependant.response_param_name] = response
    return SolvedDependency(
        values=values,
        errors=errors,
        response=response,
        dependency_cache=dependency_cache,
    )


def request_params_to_args(
    fields: Sequence[ModelField],
    received_params: Mapping[str, Any] | QueryParams | Headers,
) -> tuple[dict[str, Any], list[Any]]:
    values: dict[str, Any] = {}
    errors: list[dict[str, Any]] = []

    if not fields:
        return values, errors

    first_field = fields[0]
    fields_to_extract = fields
    single_not_embedded_field = False
    default_convert_underscores = True
    if len(fields) == 1 and lenient_issubclass(first_field.type_, BaseModel):
        fields_to_extract = get_cached_model_fields(first_field.type_)
        single_not_embedded_field = True
        # If headers are in a Pydantic model, the way to disable convert_underscores
        # would be with Header(convert_underscores=False) at the Pydantic model level
        default_convert_underscores = getattr(
            first_field.field_info, "convert_underscores", True
        )

    params_to_process: dict[str, Any] = {}

    processed_keys = set()

    for field in fields_to_extract:
        alias = None
        if isinstance(received_params, Headers):
            # Handle fields extracted from a Pydantic Model for a header, each field
            # doesn't have a FieldInfo of type Header with the default convert_underscores=True
            convert_underscores = getattr(
                field.field_info, "convert_underscores", default_convert_underscores
            )
            if convert_underscores:
                alias = (
                    field.alias
                    if field.alias != field.name
                    else field.name.replace("_", "-")
                )
        value = _get_multidict_value(field, received_params, alias=alias)
        if value is not None:
            params_to_process[field.name] = value
        processed_keys.add(alias or field.alias)
        processed_keys.add(field.name)

    for key, value in received_params.items():
        if key not in processed_keys:
            params_to_process[key] = value

    if single_not_embedded_field:
        field_info = first_field.field_info
        assert isinstance(field_info, params.Param), (
            "Params must be subclasses of Param"
        )
        loc: tuple[str, ...] = (field_info.in_.value,)
        v_, errors_ = _validate_value_with_model_field(
            field=first_field, value=params_to_process, values=values, loc=loc
        )
        return {first_field.name: v_}, errors_

    for field in fields:
        value = _get_multidict_value(field, received_params)
        field_info = field.field_info
        assert isinstance(field_info, params.Param), (
            "Params must be subclasses of Param"
        )
        loc = (field_info.in_.value, field.alias)
        v_, errors_ = _validate_value_with_model_field(
            field=field, value=value, values=values, loc=loc
        )
        if errors_:
            errors.extend(errors_)
        else:
            values[field.name] = v_
    return values, errors


def _get_multidict_value(
    field: ModelField, values: Mapping[str, Any], alias: str | None = None
) -> Any:
    alias = alias or field.alias
    if is_sequence_field(field) and isinstance(values, ImmutableMultiDict | Headers):
        value = values.getlist(alias)
    else:
        value = values.get(alias, None)
    if value is None or (is_sequence_field(field) and len(value) == 0):
        if field.required:
            return
        else:
            return deepcopy(field.default)
    return value


def is_sequence_field(field: ModelField) -> bool:
    return field_annotation_is_sequence(field.field_info.annotation)


def _validate_value_with_model_field(
    *, field: ModelField, value: Any, values: dict[str, Any], loc: tuple[str, ...]
) -> tuple[Any, list[Any]]:
    if value is None:
        if field.required:
            return None, [get_missing_field_error(loc=loc)]
        else:
            return deepcopy(field.default), []
    v_, errors_ = field.validate(value, values, loc=loc)
    if isinstance(errors_, Exception):
        return None, [errors_]
    elif isinstance(errors_, list):
        new_errors = _regenerate_error_with_loc(errors=errors_, loc_prefix=())
        return None, new_errors
    else:
        return v_, []


def get_missing_field_error(loc: tuple[str, ...]) -> dict[str, Any]:
    error = ValidationError.from_exception_data(
        "Field required", [{"type": "missing", "loc": loc, "input": {}}]
    ).errors(include_url=False)[0]
    error["input"] = None
    return error  # type: ignore[return-value]


def request_body_to_args(
    body_fields: list[ModelField],
    received_body: dict[str, Any] | None,
    embed_body_fields: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    values: dict[str, Any] = {}
    errors: list[dict[str, Any]] = []
    assert body_fields, "request_body_to_args() should be called with fields"
    single_not_embedded_field = len(body_fields) == 1 and not embed_body_fields
    first_field = body_fields[0]
    body_to_process = received_body

    if single_not_embedded_field:
        loc: tuple[str, ...] = ("body",)
        v_, errors_ = _validate_value_with_model_field(
            field=first_field, value=body_to_process, values=values, loc=loc
        )
        return {first_field.name: v_}, errors_
    for field in body_fields:
        loc = ("body", field.alias)
        value: Any = None
        if body_to_process is not None:
            try:
                value = body_to_process.get(field.alias)
            # If the received body is a list, not a dict
            except AttributeError:
                errors.append(get_missing_field_error(loc))
                continue
        v_, errors_ = _validate_value_with_model_field(
            field=field, value=value, values=values, loc=loc
        )
        if errors_:
            errors.extend(errors_)
        else:
            values[field.name] = v_
    return values, errors


@lru_cache
def get_cached_model_fields(model: type[BaseModel]) -> list[ModelField]:
    return [
        ModelField(field_info=field_info, name=name)
        for name, field_info in model.model_fields.items()
    ]


def get_typed_return_annotation(call: Callable[..., Any]) -> Any:
    signature = inspect.signature(call)
    annotation = signature.return_annotation

    if annotation is inspect.Signature.empty:
        return None

    globalns = getattr(call, "__globals__", {})
    return get_typed_annotation(annotation, globalns)


def _get_flat_fields_from_params(fields: List[ModelField]) -> List[ModelField]:
    if not fields:
        return fields
    first_field = fields[0]
    if len(fields) == 1 and lenient_issubclass(first_field.type_, BaseModel):
        fields_to_extract = get_cached_model_fields(first_field.type_)
        return fields_to_extract
    return fields


def get_flat_params(dependant: Dependant) -> List[ModelField]:
    flat_dependant = get_flat_dependant(dependant, skip_repeats=True)
    path_params = _get_flat_fields_from_params(flat_dependant.path_params)
    query_params = _get_flat_fields_from_params(flat_dependant.query_params)
    header_params = _get_flat_fields_from_params(flat_dependant.header_params)
    cookie_params = _get_flat_fields_from_params(flat_dependant.cookie_params)
    return path_params + query_params + header_params + cookie_params
