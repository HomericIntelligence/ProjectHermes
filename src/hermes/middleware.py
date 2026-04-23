"""HTTP middleware for ProjectHermes."""

from __future__ import annotations

from typing import Callable, Awaitable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp


class PayloadSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose body exceeds *max_bytes* with HTTP 413."""

    def __init__(self, app: ASGIApp, max_bytes: int = 1_048_576) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        content_length = request.headers.get("Content-Length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_bytes:
                    return Response(status_code=413)
            except ValueError:
                pass

        body = await request.body()
        if len(body) > self.max_bytes:
            return Response(status_code=413)

        return await call_next(request)
