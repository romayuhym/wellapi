"""Move security models here. For avoid import all openapi models in a project"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class BaseModelWithConfig(BaseModel):
    model_config = {"extra": "allow"}


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
