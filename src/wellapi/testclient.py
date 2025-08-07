import asyncio
import typing

import httpx


class _TestByteStream(httpx.SyncByteStream):
    def __init__(self, body: list[bytes]) -> None:
        self._body = body

    def __iter__(self) -> typing.Iterator[bytes]:
        yield b"".join(self._body)


class _TestClientTransport(httpx.ASGITransport):
    def handle_request(self, request: httpx.Request):
        resp = asyncio.run(self.handle_async_request(request))

        resp.stream = _TestByteStream(resp.stream._body)

        return resp


class TestClient(httpx.Client):
    def __init__(
        self,
        app,
        base_url: str = "http://testserver",
        headers: dict[str, str] | None = None,
        client: tuple[str, int] = ("testclient", 50000),
    ) -> None:
        transport = _TestClientTransport(
            app=app,
            client=client
        )
        asyncio.run(app({"type": "lifespan"}, receive=lambda: {}, send=lambda _: None))
        if headers is None:
            headers = {}
        headers.setdefault("user-agent", "testclient")
        super().__init__(
            base_url=base_url,
            headers=headers,
            transport=transport,
        )
