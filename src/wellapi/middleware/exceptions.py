from __future__ import annotations

import typing

from wellapi.exceptions import HTTPException
from wellapi.models import RequestAPIGateway, ResponseAPIGateway


def _lookup_exception_handler(exc_handlers, exc: Exception):
    for cls in type(exc).__mro__:
        if cls in exc_handlers:
            return exc_handlers[cls]
    return None


class ExceptionMiddleware:
    def __init__(
        self,
        next_call: typing.Callable,
        handlers: typing.Mapping[
            typing.Any,
            typing.Callable[[RequestAPIGateway, Exception], ResponseAPIGateway],
        ]
        | None = None,
        debug: bool = False,
    ) -> None:
        self.next_call = next_call
        self.debug = debug  # TODO: We ought to handle 404 cases if debug is set.
        self._status_handlers = {}
        self._exception_handlers = {
            HTTPException: self.http_exception,
        }
        if handlers is not None:  # pragma: no branch
            for key, value in handlers.items():
                self.add_exception_handler(key, value)

    def add_exception_handler(
        self,
        exc_class_or_status_code: int | type[Exception],
        handler: typing.Callable[[RequestAPIGateway, Exception], ResponseAPIGateway],
    ) -> None:
        if isinstance(exc_class_or_status_code, int):
            self._status_handlers[exc_class_or_status_code] = handler
        else:
            assert issubclass(exc_class_or_status_code, Exception)
            self._exception_handlers[exc_class_or_status_code] = handler

    def __call__(self, request: RequestAPIGateway) -> ResponseAPIGateway:
        try:
            response = self.next_call(request)
        except Exception as exc:
            handler = None

            if isinstance(exc, HTTPException):
                handler = self._status_handlers.get(exc.status_code)

            if handler is None:
                handler = _lookup_exception_handler(self._exception_handlers, exc)

            if handler is None:
                raise exc

            response = handler(request, exc)

        return response

    def http_exception(
        self, _: RequestAPIGateway, exc: Exception
    ) -> ResponseAPIGateway:
        assert isinstance(exc, HTTPException)

        return ResponseAPIGateway(
            exc.detail, status_code=exc.status_code, headers=exc.headers
        )
