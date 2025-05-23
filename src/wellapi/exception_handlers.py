from wellapi.exceptions import HTTPException, RequestValidationError
from wellapi.models import RequestAPIGateway, ResponseAPIGateway


def http_exception_handler(request: RequestAPIGateway, exc: HTTPException) -> ResponseAPIGateway:
    headers = getattr(exc, "headers", None)
    return ResponseAPIGateway(
        {"detail": exc.detail}, status_code=exc.status_code, headers=headers
    )


def request_validation_exception_handler(
    request: RequestAPIGateway, exc: RequestValidationError
) -> ResponseAPIGateway:
    return ResponseAPIGateway(
        status_code=422,
        content={"detail": exc.errors()},
    )
