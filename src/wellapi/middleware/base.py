import typing
from collections.abc import Callable

from wellapi.models import RequestAPIGateway, ResponseAPIGateway


class BaseMiddleware:
    def __init__(self, next_call: Callable, dispatch: Callable | None = None) -> None:
        self.next_call = next_call
        self.dispatch_func = self.dispatch if dispatch is None else dispatch

    def __call__(self, request: RequestAPIGateway) -> ResponseAPIGateway:
        return self.dispatch_func(request, self.next_call)

    def dispatch(
        self, request: RequestAPIGateway, call_next: typing.Callable
    ) -> ResponseAPIGateway:
        raise NotImplementedError()  # pragma: no cover
