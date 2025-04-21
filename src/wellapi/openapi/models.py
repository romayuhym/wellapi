from enum import Enum
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import AnyUrl, BaseModel, EmailStr, Field
from typing_extensions import TypedDict

METHODS_WITH_BODY = {"GET", "HEAD", "POST", "PUT", "DELETE", "PATCH"}
REF_PREFIX = "#/components/schemas/"
REF_TEMPLATE = "#/components/schemas/{model}"


class BaseModelWithConfig(BaseModel):
    model_config = {"extra": "allow"}


class Contact(BaseModelWithConfig):
    name: str | None = None
    url: AnyUrl | None = None
    email: EmailStr | None = None


class License(BaseModelWithConfig):
    name: str
    url: AnyUrl | None = None


class Info(BaseModelWithConfig):
    title: str
    description: str | None = None
    termsOfService: str | None = None
    contact: Contact | None = None
    license: License | None = None
    version: str


class ServerVariable(BaseModelWithConfig):
    enum: Annotated[list[str] | None, Field(min_length=1)] = None
    default: str
    description: str | None = None


class Server(BaseModelWithConfig):
    url: AnyUrl | str
    description: str | None = None
    variables: dict[str, ServerVariable] | None = None


class Reference(BaseModel):
    ref: str = Field(alias="$ref")


class Discriminator(BaseModel):
    propertyName: str
    mapping: dict[str, str] | None = None


class XML(BaseModelWithConfig):
    name: str | None = None
    namespace: str | None = None
    prefix: str | None = None
    attribute: bool | None = None
    wrapped: bool | None = None


class ExternalDocumentation(BaseModelWithConfig):
    description: str | None = None
    url: AnyUrl


class Schema(BaseModelWithConfig):
    # Ref: JSON Schema 2020-12: https://json-schema.org/draft/2020-12/json-schema-core.html#name-the-json-schema-core-vocabu
    # Core Vocabulary
    schema_: str | None = Field(default=None, alias="$schema")
    vocabulary: str | None = Field(default=None, alias="$vocabulary")
    id: str | None = Field(default=None, alias="$id")
    anchor: str | None = Field(default=None, alias="$anchor")
    dynamicAnchor: str | None = Field(default=None, alias="$dynamicAnchor")
    ref: str | None = Field(default=None, alias="$ref")
    dynamicRef: str | None = Field(default=None, alias="$dynamicRef")
    defs: dict[str, "SchemaOrBool"] | None = Field(default=None, alias="$defs")
    comment: str | None = Field(default=None, alias="$comment")
    # Ref: JSON Schema 2020-12: https://json-schema.org/draft/2020-12/json-schema-core.html#name-a-vocabulary-for-applying-s
    # A Vocabulary for Applying Subschemas
    allOf: list["SchemaOrBool"] | None = None
    anyOf: list["SchemaOrBool"] | None = None
    oneOf: list["SchemaOrBool"] | None = None
    not_: Optional["SchemaOrBool"] = Field(default=None, alias="not")
    if_: Optional["SchemaOrBool"] = Field(default=None, alias="if")
    then: Optional["SchemaOrBool"] = None
    else_: Optional["SchemaOrBool"] = Field(default=None, alias="else")
    dependentSchemas: dict[str, "SchemaOrBool"] | None = None
    prefixItems: list["SchemaOrBool"] | None = None
    # TODO: uncomment and remove below when deprecating Pydantic v1
    # It generales a list of schemas for tuples, before prefixItems was available
    # items: Optional["SchemaOrBool"] = None
    items: Union["SchemaOrBool", list["SchemaOrBool"]] | None = None
    contains: Optional["SchemaOrBool"] = None
    properties: dict[str, "SchemaOrBool"] | None = None
    patternProperties: dict[str, "SchemaOrBool"] | None = None
    additionalProperties: Optional["SchemaOrBool"] = None
    propertyNames: Optional["SchemaOrBool"] = None
    unevaluatedItems: Optional["SchemaOrBool"] = None
    unevaluatedProperties: Optional["SchemaOrBool"] = None
    # Ref: JSON Schema Validation 2020-12: https://json-schema.org/draft/2020-12/json-schema-validation.html#name-a-vocabulary-for-structural
    # A Vocabulary for Structural Validation
    type: str | None = None
    enum: list[Any] | None = None
    const: Any | None = None
    multipleOf: float | None = Field(default=None, gt=0)
    maximum: float | None = None
    exclusiveMaximum: float | None = None
    minimum: float | None = None
    exclusiveMinimum: float | None = None
    maxLength: int | None = Field(default=None, ge=0)
    minLength: int | None = Field(default=None, ge=0)
    pattern: str | None = None
    maxItems: int | None = Field(default=None, ge=0)
    minItems: int | None = Field(default=None, ge=0)
    uniqueItems: bool | None = None
    maxContains: int | None = Field(default=None, ge=0)
    minContains: int | None = Field(default=None, ge=0)
    maxProperties: int | None = Field(default=None, ge=0)
    minProperties: int | None = Field(default=None, ge=0)
    required: list[str] | None = None
    dependentRequired: dict[str, set[str]] | None = None
    # Ref: JSON Schema Validation 2020-12: https://json-schema.org/draft/2020-12/json-schema-validation.html#name-vocabularies-for-semantic-c
    # Vocabularies for Semantic Content With "format"
    format: str | None = None
    # Ref: JSON Schema Validation 2020-12: https://json-schema.org/draft/2020-12/json-schema-validation.html#name-a-vocabulary-for-the-conten
    # A Vocabulary for the Contents of String-Encoded Data
    contentEncoding: str | None = None
    contentMediaType: str | None = None
    contentSchema: Optional["SchemaOrBool"] = None
    # Ref: JSON Schema Validation 2020-12: https://json-schema.org/draft/2020-12/json-schema-validation.html#name-a-vocabulary-for-basic-meta
    # A Vocabulary for Basic Meta-Data Annotations
    title: str | None = None
    description: str | None = None
    default: Any | None = None
    deprecated: bool | None = None
    readOnly: bool | None = None
    writeOnly: bool | None = None
    examples: list[Any] | None = None
    # Ref: OpenAPI 3.1.0: https://github.com/OAI/OpenAPI-Specification/blob/main/versions/3.1.0.md#schema-object
    # Schema Object
    discriminator: Discriminator | None = None
    xml: XML | None = None
    externalDocs: ExternalDocumentation | None = None


# Ref: https://json-schema.org/draft/2020-12/json-schema-core.html#name-json-schema-documents
# A JSON Schema MUST be an object or a boolean.
SchemaOrBool = Schema | bool


class Example(TypedDict, total=False):
    __pydantic_config__ = {"extra": "allow"}

    summary: str | None
    description: str | None
    value: Any | None
    externalValue: AnyUrl | None


class ParameterInType(Enum):
    query = "query"
    header = "header"
    path = "path"
    cookie = "cookie"


class Encoding(BaseModelWithConfig):
    contentType: str | None = None
    headers: dict[str, Union["Header", Reference]] | None = None
    style: str | None = None
    explode: bool | None = None
    allowReserved: bool | None = None


class MediaType(BaseModelWithConfig):
    schema_: Schema | Reference | None = Field(default=None, alias="schema")
    example: Any | None = None
    examples: dict[str, Example | Reference] | None = None
    encoding: dict[str, Encoding] | None = None


class ParameterBase(BaseModelWithConfig):
    description: str | None = None
    required: bool | None = None
    deprecated: bool | None = None
    # Serialization rules for simple scenarios
    style: str | None = None
    explode: bool | None = None
    allowReserved: bool | None = None
    schema_: Schema | Reference | None = Field(default=None, alias="schema")
    example: Any | None = None
    examples: dict[str, Example | Reference] | None = None
    # Serialization rules for more complex scenarios
    content: dict[str, MediaType] | None = None


class Parameter(ParameterBase):
    name: str
    in_: ParameterInType = Field(alias="in")


class Header(ParameterBase):
    pass


class RequestBody(BaseModelWithConfig):
    description: str | None = None
    content: dict[str, MediaType]
    required: bool | None = None


class Link(BaseModelWithConfig):
    operationRef: str | None = None
    operationId: str | None = None
    parameters: dict[str, Any | str] | None = None
    requestBody: Any | str | None = None
    description: str | None = None
    server: Server | None = None


class Response(BaseModelWithConfig):
    description: str
    headers: dict[str, Header | Reference] | None = None
    content: dict[str, MediaType] | None = None
    links: dict[str, Link | Reference] | None = None


class AmazonApiGatewayIntegration(BaseModelWithConfig):
    httpMethod: str | None = None
    type: str
    uri: str | dict | None = None
    cacheKeyParameters: list[str] | None = None
    cacheNamespace: str | None = None
    responses: dict[str, Any] | None = None
    requestTemplates: dict[str, Any] | None = None


class RequestValidators(Enum):
    basic = "basic"
    paramsOnly = "params-only"
    bodyOnly = "body-only"


class Operation(BaseModelWithConfig):
    tags: list[str] | None = None
    summary: str | None = None
    description: str | None = None
    externalDocs: ExternalDocumentation | None = None
    operationId: str | None = None
    parameters: list[Parameter | Reference] | None = None
    requestBody: RequestBody | Reference | None = None
    # Using Any for Specification Extensions
    responses: dict[str, Response | Any] | None = None
    callbacks: dict[str, dict[str, "PathItem"] | Reference] | None = None
    deprecated: bool | None = None
    security: list[dict[str, list[str]]] | None = None
    servers: list[Server] | None = None
    # Using for AWS integration
    amazonApiGatewayIntegration: AmazonApiGatewayIntegration = Field(
        alias="x-amazon-apigateway-integration"
    )
    amazonApiGatewayRequestValidator: RequestValidators | None = Field(
        default=None, alias="x-amazon-apigateway-request-validator"
    )


class PathItem(BaseModelWithConfig):
    ref: str | None = Field(default=None, alias="$ref")
    summary: str | None = None
    description: str | None = None
    get: Operation | None = None
    put: Operation | None = None
    post: Operation | None = None
    delete: Operation | None = None
    options: Operation | None = None
    head: Operation | None = None
    patch: Operation | None = None
    trace: Operation | None = None
    servers: list[Server] | None = None
    parameters: list[Parameter | Reference] | None = None


class SecuritySchemeType(Enum):
    apiKey = "apiKey"
    http = "http"
    oauth2 = "oauth2"
    openIdConnect = "openIdConnect"


class SecurityBase(BaseModelWithConfig):
    type_: SecuritySchemeType = Field(alias="type")
    description: str | None = None


class APIKeyIn(Enum):
    query = "query"
    header = "header"
    cookie = "cookie"


class APIKey(SecurityBase):
    type_: SecuritySchemeType = Field(default=SecuritySchemeType.apiKey, alias="type")
    in_: APIKeyIn = Field(alias="in")
    name: str
    amazonApiGatewayApiKeySource: str | None = Field(
        default="HEADER", alias="x-amazon-apigateway-api-key-source"
    )


class HTTPBase(SecurityBase):
    type_: SecuritySchemeType = Field(default=SecuritySchemeType.http, alias="type")
    scheme: str


class HTTPBearer(HTTPBase):
    scheme: Literal["bearer"] = "bearer"
    bearerFormat: str | None = None


class OAuthFlow(BaseModelWithConfig):
    refreshUrl: str | None = None
    scopes: dict[str, str] = {}


class OAuthFlowImplicit(OAuthFlow):
    authorizationUrl: str


class OAuthFlowPassword(OAuthFlow):
    tokenUrl: str


class OAuthFlowClientCredentials(OAuthFlow):
    tokenUrl: str


class OAuthFlowAuthorizationCode(OAuthFlow):
    authorizationUrl: str
    tokenUrl: str


class OAuthFlows(BaseModelWithConfig):
    implicit: OAuthFlowImplicit | None = None
    password: OAuthFlowPassword | None = None
    clientCredentials: OAuthFlowClientCredentials | None = None
    authorizationCode: OAuthFlowAuthorizationCode | None = None


class OAuth2(SecurityBase):
    type_: SecuritySchemeType = Field(default=SecuritySchemeType.oauth2, alias="type")
    flows: OAuthFlows


class OpenIdConnect(SecurityBase):
    type_: SecuritySchemeType = Field(
        default=SecuritySchemeType.openIdConnect, alias="type"
    )
    openIdConnectUrl: str


SecurityScheme = APIKey | HTTPBase | OAuth2 | OpenIdConnect | HTTPBearer


class Components(BaseModelWithConfig):
    schemas: dict[str, Schema | Reference] | None = None
    responses: dict[str, Response | Reference] | None = None
    parameters: dict[str, Parameter | Reference] | None = None
    examples: dict[str, Example | Reference] | None = None
    requestBodies: dict[str, RequestBody | Reference] | None = None
    headers: dict[str, Header | Reference] | None = None
    securitySchemes: dict[str, SecurityScheme | Reference] | None = None
    links: dict[str, Link | Reference] | None = None
    # Using Any for Specification Extensions
    callbacks: dict[str, dict[str, PathItem] | Reference | Any] | None = None


class Tag(BaseModelWithConfig):
    name: str
    description: str | None = None
    externalDocs: ExternalDocumentation | None = None


class AmazonRequestValidator(BaseModelWithConfig):
    validateRequestBody: bool
    validateRequestParameters: bool


class OpenAPI(BaseModelWithConfig):
    openapi: str
    info: Info
    amazonApiGatewayRequestValidators: dict[
        RequestValidators, AmazonRequestValidator
    ] = Field(alias="x-amazon-apigateway-request-validators")
    amazonApiGatewayGatewayResponses: dict[str, Any] | None = Field(
        default=None, alias="x-amazon-apigateway-gateway-responses"
    )
    servers: list[Server] | None = None
    # Using Any for Specification Extensions
    paths: dict[str, PathItem | Any] | None = None
    components: Components | None = None
    security: list[dict[str, list[str]]] | None = None
    tags: list[Tag] | None = None
    externalDocs: ExternalDocumentation | None = None


Schema.model_rebuild()
Operation.model_rebuild()
Encoding.model_rebuild()
