"""Bound and replay small local requests before JSON or multipart parsing."""

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

MAX_REQUEST_BYTES = 8_000_000


class RequestSizeLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return
        too_large = JSONResponse({"detail": "Request exceeds 8 MB"}, status_code=413)
        declared = Headers(scope=scope).get("content-length")
        if declared is not None:
            try:
                size = int(declared)
                if size < 0:
                    raise ValueError
            except ValueError:
                await JSONResponse(
                    {"detail": "Invalid content length"}, status_code=400
                )(scope, receive, send)
                return
            if size > MAX_REQUEST_BYTES:
                await too_large(scope, receive, send)
                return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > MAX_REQUEST_BYTES:
                await too_large(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        pending = True

        async def replay():
            nonlocal pending
            if pending:
                pending = False
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
