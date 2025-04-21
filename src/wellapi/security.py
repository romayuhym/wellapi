from typing import Any, cast

from wellapi.exceptions import HTTPException
from wellapi.models import RequestAPIGateway
from wellapi.openapi.models import APIKey, APIKeyIn
from wellapi.openapi.models import OAuth2 as OAuth2Model
from wellapi.openapi.models import OAuthFlows as OAuthFlowsModel
from wellapi.openapi.models import SecurityBase as SecurityBaseModel


class SecurityBase:
    model: SecurityBaseModel
    scheme_name: str


class APIKeyBase(SecurityBase):
    @staticmethod
    def check_api_key(api_key: str | None, auto_error: bool) -> str | None:
        if not api_key:
            if auto_error:
                raise HTTPException(status_code=403, detail="Not authenticated")
            return None
        return api_key


class APIKeyHeader(APIKeyBase):
    def __init__(
        self,
        *,
        name: str,
        scheme_name: str | None = None,
        description: str | None = None,
        auto_error: bool = True,
    ):
        self.model: APIKey = APIKey(
            **{"in": APIKeyIn.header},  # type: ignore[arg-type]
            name=name,
            description=description,
        )
        self.scheme_name = scheme_name or self.__class__.__name__
        self.auto_error = auto_error

    def __call__(self, request: RequestAPIGateway) -> str | None:
        api_key = request.headers.get(self.model.name)
        return self.check_api_key(api_key, self.auto_error)


class OAuth2(SecurityBase):
    def __init__(
        self,
        *,
        flows: OAuthFlowsModel | dict[str, dict[str, Any]] = OAuthFlowsModel(),
        scheme_name: str | None = None,
        description: str | None = None,
        auto_error: bool = True,
    ):
        self.model = OAuth2Model(
            flows=cast(OAuthFlowsModel, flows), description=description
        )
        self.scheme_name = scheme_name or self.__class__.__name__
        self.auto_error = auto_error

    def __call__(self, request: RequestAPIGateway) -> str | None:
        authorization = request.headers.get("Authorization")
        if not authorization:
            if self.auto_error:
                raise HTTPException(status_code=403, detail="Not authenticated")
            else:
                return None
        return authorization


class OAuth2PasswordBearer(OAuth2):
    def __init__(
        self,
        tokenUrl: str,
        scheme_name: str | None = None,
        scopes: dict[str, str] | None = None,
        description: str | None = None,
        auto_error: bool = True,
    ):
        if not scopes:
            scopes = {}
        flows = OAuthFlowsModel(
            password=cast(Any, {"tokenUrl": tokenUrl, "scopes": scopes})
        )
        super().__init__(
            flows=flows,
            scheme_name=scheme_name,
            description=description,
            auto_error=auto_error,
        )

    def __call__(self, request: RequestAPIGateway) -> str | None:
        authorization = request.headers.get("Authorization")
        scheme, param = get_authorization_scheme_param(authorization)
        if not authorization or scheme.lower() != "bearer":
            if self.auto_error:
                raise HTTPException(
                    status_code=401,
                    detail="Not authenticated",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            else:
                return None
        return param


def get_authorization_scheme_param(
    authorization_header_value: str | None,
) -> tuple[str, str]:
    if not authorization_header_value:
        return "", ""
    scheme, _, param = authorization_header_value.partition(" ")
    return scheme, param
