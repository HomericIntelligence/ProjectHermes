"""Rate limiting for the /webhook endpoint using slowapi."""

from __future__ import annotations

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address


limiter = Limiter(key_func=get_remote_address)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Response:
    """Return 429 with Retry-After header when the rate limit is exceeded."""
    retry_after = str(exc.limit.limit.get_expiry()) if exc.limit else "60"
    return JSONResponse(
        {"detail": f"Rate limit exceeded: {exc.detail}"},
        status_code=429,
        headers={"Retry-After": retry_after},
    )
