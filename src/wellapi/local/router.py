import sys
from collections.abc import Callable
from enum import Enum

from wellapi.applications import Lambda
from wellapi.routing import compile_path
from wellapi.utils import import_app, load_handlers


class Match(Enum):
    NONE = 0
    PARTIAL = 1
    FULL = 2


def get_route_path(path: str) -> str:
    root_path = ""
    if not root_path:
        return path

    if not path.startswith(root_path):
        return path

    if path == root_path:
        return ""

    if path[len(root_path)] == "/":
        return path[len(root_path) :]

    return path


class Route:
    def __init__(
        self,
        path: str,
        function: Callable,
        method: str = "GET",
        include_in_schema: bool = True,
    ):
        self.path = path
        self.method = method
        self.endpoint_module = function.__module__
        self.endpoint_name = function.__name__
        self.include_in_schema = include_in_schema
        self.path_regex, self.path_format, self.param_convertors = compile_path(path)

    def __repr__(self):
        return f"Route(path={self.path}, method={self.method})"

    def __call__(self, *args, **kwargs):
        module = sys.modules[self.endpoint_module]
        return getattr(module, self.endpoint_name)(*args, **kwargs)

    def matches(self, scope: dict, method, path) -> tuple[Match, dict]:
        route_path = get_route_path(path)
        match = self.path_regex.match(route_path)
        if match:
            matched_params = match.groupdict()
            for key, value in matched_params.items():
                matched_params[key] = self.param_convertors[key].convert(value)
            path_params = dict(scope.get("pathParameters", {}) or {})
            path_params.update(matched_params)
            child_scope = {"pathParameters": path_params}

            if self.method == method:
                return Match.FULL, child_scope

        return Match.NONE, {}


class Router:
    def __init__(self):
        self.routes: list[Route] = []

    def add_route(self, path: str, method: str, function: Callable):
        route = Route(path, function, method)
        self.routes.append(route)
        return route

    def discover_handlers(self, app_srt, path_to_handlers_dir):
        app = import_app(app_srt)
        load_handlers(path_to_handlers_dir)

        self.routes = []
        e: Lambda
        for e in app.lambdas:
            path = e.path
            method = e.method
            if e.type_ == "queue":
                path = f"/queue_/{e.path}"
                method = "POST"
            elif e.type_ == "job":
                path = f"/job_/{e.name}"
                method = "POST"

            self.add_route(path, method, e.endpoint)

    def __call__(self, scope: dict, method, path):
        for route in self.routes:
            match, child_scope = route.matches(scope, method, path)
            if match == Match.FULL:
                scope.update(child_scope)
                return route(scope, {})

        return {
            "statusCode": 404,
            "headers": {
                "Content-Type": "application/json",
                "My-Custom-Header": "Custom Value",
            },
            "body": "Not Found",
            "isBase64Encoded": False,
        }
