import re

from wellapi import RequestAPIGateway, ResponseAPIGateway


class CORSMiddleware:
    def __init__(
        self,
        app,
        allow_origins=None,
        allow_headers=None,
        allow_credentials=False,
        allow_origin_regex=None,
        expose_headers=None,
    ):
        compiled_allow_origin_regex = None
        if allow_origin_regex is not None:
            compiled_allow_origin_regex = re.compile(allow_origin_regex)

        allow_all_origins = "*" in (allow_origins or [])
        allow_all_headers = "*" in (allow_headers or [])

        simple_headers = {}
        if allow_all_origins:
            simple_headers["Access-Control-Allow-Origin"] = "*"
        if allow_all_headers:
            simple_headers["Access-Control-Allow-Headers"] = "*"
        elif allow_headers:
            simple_headers["Access-Control-Allow-Headers"] = (
                ", ".join([h.lower() for h in allow_headers])
            )
        if allow_credentials:
            simple_headers["Access-Control-Allow-Credentials"] = "true"
        if expose_headers:
            simple_headers["Access-Control-Expose-Headers"] = ", ".join(expose_headers)

        self.app = app
        self.allow_origins = allow_origins
        self.allow_all_origins = allow_all_origins
        self.allow_origin_regex = compiled_allow_origin_regex
        self.simple_headers = simple_headers

    def __call__(self, request: RequestAPIGateway) -> ResponseAPIGateway:
        response = self.app(request)

        origin = request.headers.get("Origin")
        if origin:
            if self.allow_all_origins:
                response.headers.update(self.simple_headers)
            elif self.allow_origin_regex and self.allow_origin_regex.match(origin):
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers.update(self.simple_headers)
            elif origin in self.allow_origins:
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers.update(self.simple_headers)
            else:
                response.headers.pop("Access-Control-Allow-Origin", None)

        return response
