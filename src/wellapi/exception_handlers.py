from wellapi.exceptions import HTTPException, RequestValidationError
from wellapi.models import RequestAPIGateway, ResponseAPIGateway


def http_exception_handler(request: RequestAPIGateway, exc: HTTPException) -> ResponseAPIGateway:
    headers = getattr(exc, "headers", None)
    return ResponseAPIGateway(
        {"detail": exc.detail}, status_code=exc.status_code, headers=headers
    )


def _make_serializable(obj):
    """Recursively convert non-JSON-serializable values (e.g. Exception instances) to strings."""
    if isinstance(obj, dict):
        return {k: _make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_make_serializable(item) for item in obj]
    if isinstance(obj, Exception):
        return str(obj)
    return obj


def request_validation_exception_handler(
    request: RequestAPIGateway, exc: RequestValidationError
) -> ResponseAPIGateway:
    return ResponseAPIGateway(
        status_code=422,
        content={"detail": _make_serializable(exc.errors())},
    )
