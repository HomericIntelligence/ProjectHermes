# SPDX-License-Identifier: MIT
"""FastAPI application for ProjectHermes."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
import signal
import uuid
from collections.abc import AsyncGenerator
import contextlib
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware

from hermes import __version__
from hermes.config import Settings, get_settings
from hermes.logging_config import setup_logging
from hermes.metrics import WEBHOOKS_FAILED, WEBHOOKS_RECEIVED
from hermes.middleware import PayloadSizeLimitMiddleware
from hermes.models import (
    DeadLettersResponse,
    ErrorResponse,
    HealthResponse,
    SubjectsResponse,
    TimeoutSettings,
    VersionResponse,
    WebhookAcceptedResponse,
    WebhookPayload,
)
from hermes.publisher import AGENT_EVENTS, TASK_EVENTS, Publisher, UnknownEventTypeError
from hermes.rate_limit import limiter, rate_limit_exceeded_handler
from slowapi.middleware import SlowAPIMiddleware

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shutdown state — module-level so tests can inspect/mutate directly
# ---------------------------------------------------------------------------

_shutdown_event: asyncio.Event = asyncio.Event()
_inflight: int = 0
_inflight_lock: asyncio.Lock = asyncio.Lock()

_NATS_CONNECT_TIMEOUT = 5

_REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_RE = re.compile(r"^[a-zA-Z0-9\-_]{1,128}$")

# ---------------------------------------------------------------------------
# Application lifespan
# ---------------------------------------------------------------------------


def _on_shutdown_signal(sig: signal.Signals) -> None:
    """Set the module-level shutdown event when a termination signal arrives."""
    logger.info("Received signal %s; initiating graceful shutdown", sig.name)
    _shutdown_event.set()


@contextlib.asynccontextmanager
async def _inflight_context() -> AsyncGenerator[None, None]:
    """Increment _inflight on entry and decrement it in finally, even on exception."""
    global _inflight
    async with _inflight_lock:
        _inflight += 1
    try:
        yield
    finally:
        async with _inflight_lock:
            _inflight -= 1


async def _connect_with_retries(publisher: Publisher, settings: "Settings") -> Exception | None:
    """Attempt NATS connection with retries; return the last exception or None on success."""
    last_exc: Exception | None = None
    for attempt in range(1, settings.nats_retry_attempts + 1):
        try:
            await asyncio.wait_for(
                publisher.connect(settings.nats_url, connect_timeout=settings.nats_connect_timeout),
                timeout=_NATS_CONNECT_TIMEOUT,
            )
            return None
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            will_retry = attempt < settings.nats_retry_attempts
            if will_retry:
                logger.error(
                    "NATS connect attempt %d/%d failed (%s: %s); retrying in %ss",
                    attempt,
                    settings.nats_retry_attempts,
                    type(exc).__name__,
                    exc,
                    settings.nats_retry_interval,
                )
                await asyncio.sleep(settings.nats_retry_interval)
            else:
                logger.error(
                    "NATS connect attempt %d/%d failed (%s: %s); giving up",
                    attempt,
                    settings.nats_retry_attempts,
                    type(exc).__name__,
                    exc,
                )
    return last_exc


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Connect to NATS on startup with retries; continue degraded if all attempts fail.

    If NATS is unreachable after all retry attempts the app still starts so that
    /health can return 503 and load-balancers can observe the degraded state.
    The publisher remains disconnected; /webhook will return 503 until NATS recovers.
    """
    global _shutdown_event, _inflight

    settings = get_settings()
    publisher = Publisher(enable_dead_letter=settings.enable_dead_letter)
    last_exc = await _connect_with_retries(publisher, settings)

    if last_exc is not None:
        logger.critical(
            "Could not connect to NATS at %s after %d attempts; "
            "starting in degraded mode — /health will return 503",
            settings.nats_url,
            settings.nats_retry_attempts,
        )

    # Install signal handlers so SIGTERM/SIGINT trigger graceful shutdown.
    # Silently skip in non-main threads (e.g. TestClient workers).
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _on_shutdown_signal, sig)
        except (ValueError, RuntimeError):
            # ValueError: not in main thread; RuntimeError: Python 3.14+ wraps it
            pass

    _shutdown_event = asyncio.Event()
    _inflight = 0

    setup_logging(json_format=settings.log_json)

    if not settings.webhook_secret:
        logger.warning(
            "HMAC webhook validation is DISABLED — set WEBHOOK_SECRET to enable signature verification",
            extra={"hmac_enabled": False},
        )

    if settings.hermes_host == "0.0.0.0":
        logger.warning("Server binding to 0.0.0.0 exposes all network interfaces")

    _log_startup_banner(publisher, settings)
    app.state.publisher = publisher
    try:
        yield
    finally:
        # Drain in-flight requests before disconnecting NATS
        deadline = settings.shutdown_timeout
        poll_interval = 0.05
        elapsed = 0.0
        logger.info("Shutdown: waiting up to %.1fs for in-flight requests to complete", deadline)
        while elapsed < deadline:
            async with _inflight_lock:
                remaining = _inflight
            if remaining == 0:
                break
            logger.debug("Shutdown: %d request(s) still in flight, waiting...", remaining)
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        else:
            # Report the number of abandoned requests so operators can correlate
            # drain timeouts with upstream impact. See #441.
            async with _inflight_lock:
                abandoned = _inflight
            logger.warning(
                "Shutdown: timed out with %d request(s) still in flight; proceeding",
                abandoned,
            )

        logger.info("Shutdown: draining NATS connection")
        await publisher.disconnect()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="ProjectHermes",
    description="Bridges external webhooks to NATS JetStream.",
    version=__version__,
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(429, rate_limit_exceeded_handler)  # type: ignore[arg-type]


@app.exception_handler(HTTPException)
async def _http_exception_handler_with_request_id(
    request: Request, exc: HTTPException
) -> JSONResponse:
    """Augment HTTPException responses with request_id from request.state when available."""
    request_id: str | None = getattr(request.state, "request_id", None)
    response = await http_exception_handler(request, exc)
    body: dict[str, object] = {"detail": exc.detail, "request_id": request_id}
    # Copy headers from the upstream response, but drop ``content-length`` —
    # the upstream response was sized for the original ``{"detail": ...}`` body,
    # whereas our augmented body also includes ``request_id`` and is therefore
    # larger. Leaving the stale Content-Length in place trips uvicorn's
    # ``Response content longer than Content-Length`` invariant via
    # ``BaseHTTPMiddleware``'s streaming wrapper (observed in readonly-fs-smoke).
    # ``JSONResponse`` will recompute Content-Length from the new body when the
    # header is absent.
    forwarded_headers = {
        k: v for k, v in response.headers.items() if k.lower() != "content-length"
    }
    return JSONResponse(
        status_code=exc.status_code,
        content=body,
        headers=forwarded_headers,
    )


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

SettingsDep = Annotated[Settings, Depends(get_settings)]


async def _require_dead_letter_key(
    settings: SettingsDep,
    x_dead_letter_key: str = Header(default=""),
) -> None:
    """Enforce API key auth for dead-letter endpoints.

    When ``dead_letter_api_key`` is unset the check is bypassed (opt-in).
    A timing-safe comparison prevents key enumeration via response timing.

    .. note::
       Use ``str = Header(default="")`` rather than ``Annotated[str, Header()]`` here. With
       ``from __future__ import annotations`` (active in this module), ``Annotated`` parameters
       are evaluated as forward references inside FastAPI's ``Depends()`` machinery and raise
       ``PydanticUserError`` at app startup on Python 3.14+. The plain default-arg form sidesteps
       the forward-reference resolution entirely. See issue #518.
    """
    if not settings.dead_letter_api_key:
        return
    if not x_dead_letter_key or not hmac.compare_digest(
        x_dead_letter_key, settings.dead_letter_api_key
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing dead-letter API key",
            headers={"WWW-Authenticate": 'Bearer realm="hermes"'},
        )


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class RequestIDMiddleware(BaseHTTPMiddleware):  # type: ignore[misc]
    """Assign a unique request ID to every incoming request.

    Reads ``X-Request-ID`` from the incoming headers if present (pass-through),
    otherwise generates a fresh UUID4. The ID is stored on ``request.state``
    so route handlers can include it in NATS payloads and log messages, and is
    echoed back to the caller via the ``X-Request-ID`` response header.
    """

    async def dispatch(self, request: Request, call_next: object) -> Response:
        raw_id = request.headers.get(_REQUEST_ID_HEADER, "")
        request_id = raw_id if _REQUEST_ID_RE.match(raw_id) else str(uuid.uuid4())
        request.state.request_id = request_id
        response: Response = await call_next(request)  # type: ignore[operator]
        response.headers[_REQUEST_ID_HEADER] = request_id
        return response


class ShutdownMiddleware(BaseHTTPMiddleware):  # type: ignore[misc]
    """Reject /webhook with 503 once shutdown has been signalled.

    All other paths (e.g. /health) pass through so load balancers can observe
    the shutting_down flag before removing the instance from rotation.
    """

    async def dispatch(self, request: Request, call_next: object) -> Response:
        if _shutdown_event.is_set() and request.url.path == "/webhook":
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": "Service is shutting down"},
            )
        response: Response = await call_next(request)  # type: ignore[operator]
        return response


app.add_middleware(ShutdownMiddleware)
app.add_middleware(RequestIDMiddleware)
# NOTE: ``max_bytes`` is captured from ``get_settings()`` at module-load time, *outside* any
# FastAPI ``Depends`` context. As a consequence, ``app.dependency_overrides[get_settings]`` does
# **not** affect this middleware during tests — tests that need a non-default
# ``max_payload_bytes`` must set the env var (or monkey-patch ``get_settings``) *before* importing
# ``hermes.server``. See issue #455 for the discussion and rationale.
app.add_middleware(PayloadSizeLimitMiddleware, max_bytes=get_settings().max_payload_bytes)
app.add_middleware(SlowAPIMiddleware)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get(
    "/health",
    response_model=HealthResponse,
    responses={503: {"model": ErrorResponse, "description": "NATS not reachable"}},
)
async def health(response: Response) -> HealthResponse:
    """Return service health and NATS connection status. Returns 503 when NATS is disconnected."""
    publisher: Publisher = app.state.publisher
    connected = publisher.is_connected
    if not connected:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    cfg = get_settings()
    dl_depth = publisher.dead_letter_count
    dl_capacity = cfg.dead_letter_max_size
    dl_threshold_pct = (dl_depth / dl_capacity) * 100.0 if dl_capacity > 0 else 0.0
    return HealthResponse(
        status="ok" if connected else "degraded",
        nats_connected=connected,
        shutting_down=_shutdown_event.is_set(),
        hmac_validation_enabled=bool(cfg.webhook_secret),
        hermes_public_url=cfg.hermes_public_url,
        inflight_requests=_inflight,
        dead_letter_count=dl_depth,
        dead_letter_queue_depth=dl_depth,
        dead_letter_queue_capacity=dl_capacity,
        dead_letter_queue_alert_threshold_pct=dl_threshold_pct,
        timeouts=TimeoutSettings(
            nats_connect=cfg.nats_connect_timeout,
            nats_publish=cfg.nats_publish_timeout,
        ),
        nats_reconnect_count=publisher.reconnect_count,
        nats_last_error=publisher.last_error,
        nats_retry_attempts=cfg.nats_retry_attempts,
        nats_retry_interval=float(cfg.nats_retry_interval),
        last_reconnect_attempt_at=publisher.last_reconnect_attempt_at,
        consecutive_reconnect_failures=publisher.consecutive_reconnect_failures,
        nats_reconnect_loop_active=publisher.reconnect_loop_active,
    )


@app.get("/version", response_model=VersionResponse)
async def get_version() -> VersionResponse:
    """Return the installed package version."""
    return VersionResponse(version=__version__)


@app.get(
    "/ready",
    responses={503: {"model": ErrorResponse, "description": "NATS not connected"}},
)
async def ready(response: Response) -> dict[str, object]:
    """Readiness probe — returns 503 when NATS is not connected."""
    publisher: Publisher = app.state.publisher
    if not publisher.is_connected:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"ready": False, "reason": "NATS not connected"}
    return {"ready": True}


@app.post(
    "/webhook",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=WebhookAcceptedResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Invalid webhook signature"},
        422: {"model": ErrorResponse, "description": "Malformed or invalid payload"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
        503: {"model": ErrorResponse, "description": "NATS publisher not connected"},
    },
)
@limiter.limit(lambda: get_settings().webhook_rate_limit)
async def receive_webhook(request: Request, settings: SettingsDep) -> WebhookAcceptedResponse:
    """Receive an external webhook, validate its signature, and publish to NATS."""
    return await _handle_webhook(request, settings)


async def _handle_webhook(request: Request, settings: Settings) -> WebhookAcceptedResponse:
    raw_body = await request.body()
    request_id: str = request.state.request_id

    async with _inflight_context():
        # HMAC validation (skipped when no secret is configured)
        if settings.webhook_secret:
            _verify_signature(raw_body, request.headers.get("X-Webhook-Signature", ""), settings)

        try:
            payload = WebhookPayload.model_validate_json(raw_body)
        except Exception as exc:
            logger.warning("Invalid webhook payload: %s", exc, extra={"request_id": request_id})
            WEBHOOKS_FAILED.labels(reason="invalid_payload").inc()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Invalid payload format",
            ) from exc

        WEBHOOKS_RECEIVED.labels(event_type=payload.event).inc()

        publisher: Publisher = app.state.publisher
        if not publisher.is_connected:
            logger.error(
                "NATS not connected; cannot publish event %r",
                payload.event,
                extra={"request_id": request_id},
            )
            WEBHOOKS_FAILED.labels(reason="nats_not_connected").inc()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="NATS publisher not connected",
            )

        try:
            await publisher.publish(
                payload,
                publish_timeout=settings.nats_publish_timeout,
                request_id=request_id,
            )
        except asyncio.TimeoutError:
            logger.error(
                "NATS publish timed out for event %r",
                payload.event,
                extra={"request_id": request_id},
            )
            WEBHOOKS_FAILED.labels(reason="publish_timeout").inc()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="NATS publish timed out",
            )
        except UnknownEventTypeError as exc:
            WEBHOOKS_FAILED.labels(reason="unknown_event_type").inc()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Unknown event type: {exc}",
            ) from exc
        return WebhookAcceptedResponse(
            status="accepted", event=payload.event, request_id=request_id
        )


@app.get(
    "/subjects",
    response_model=SubjectsResponse,
)
@limiter.limit(lambda: get_settings().subjects_rate_limit)
async def list_subjects(request: Request, settings: SettingsDep) -> SubjectsResponse:
    """Return the list of NATS subjects that have been published to."""
    publisher: Publisher = app.state.publisher
    return SubjectsResponse(
        subjects=publisher.active_subjects,
        hermes_public_url=settings.hermes_public_url or "",
        active_subjects_max=publisher.active_subjects_max,
    )


@app.get("/metrics")
async def metrics() -> Response:
    """Expose Prometheus metrics in text format."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/dead-letters", response_model=DeadLettersResponse)
async def dead_letters(
    offset: int = 0,
    limit: int | None = None,
    _: None = Depends(_require_dead_letter_key),
) -> DeadLettersResponse:
    """Return the in-memory dead-letter queue with optional pagination.

    Query params:
    - ``offset``: number of items to skip (default 0).
    - ``limit``: maximum items to return (default: ``dead_letter_page_size_default``;
      hard ceiling: ``dead_letter_page_size_max``).
    """
    settings = get_settings()
    effective_limit: int = limit if limit is not None else settings.dead_letter_page_size_default
    if effective_limit > settings.dead_letter_page_size_max:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"limit must not exceed {settings.dead_letter_page_size_max}; got {effective_limit}"
            ),
        )
    publisher: Publisher = app.state.publisher
    all_items = publisher.dead_letters
    total = len(all_items)
    sliced = all_items[offset : offset + effective_limit]
    return DeadLettersResponse(total=total, offset=offset, limit=effective_limit, items=sliced)


@app.delete("/dead-letters", status_code=status.HTTP_200_OK)
async def drain_dead_letters(
    _: None = Depends(_require_dead_letter_key),
) -> dict[str, int]:
    """Drain (clear) the in-memory dead-letter queue and return the count of drained items."""
    publisher: Publisher = app.state.publisher
    drained = publisher.drain_dead_letters()
    return {"drained": drained}


@app.get("/events")
async def list_events() -> dict[str, list[str]]:
    """Return the canonical set of supported webhook event types."""
    agent = sorted(AGENT_EVENTS)
    task = sorted(TASK_EVENTS)
    return {
        "agent_events": agent,
        "task_events": task,
        "all_events": sorted(AGENT_EVENTS | TASK_EVENTS),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mask_secret(value: str, show_chars: int = 4) -> str:
    """Mask a secret value, showing only the first ``show_chars`` characters."""
    if not value:
        return "(not set)"
    if len(value) <= show_chars:
        return "****"
    return value[:show_chars] + "****"


def _log_startup_banner(publisher: Publisher, settings: Settings | None = None) -> None:
    """Log version, active configuration, and NATS connectivity on startup."""
    if settings is None:
        settings = get_settings()
    logger.info("hermes version=%s", __version__)
    logger.info(
        "config nats_url=%s port=%s public_url=%s",
        settings.nats_url,
        settings.hermes_port,
        settings.hermes_public_url,
    )
    logger.info(
        "secrets webhook_secret=%s",
        _mask_secret(settings.webhook_secret),
    )
    logger.info(
        "hmac_validation=%s",
        "enabled" if settings.webhook_secret else "disabled",
    )
    logger.info(
        "nats connected=%s streams=%s",
        publisher.is_connected,
        publisher.stream_names,
    )


def _verify_signature(body: bytes, provided: str, settings: Settings) -> None:
    """Raise HTTP 401 if the HMAC-SHA256 signature does not match."""
    expected = hmac.new(
        settings.webhook_secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, provided):
        WEBHOOKS_FAILED.labels(reason="invalid_signature").inc()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
            headers={"WWW-Authenticate": 'Bearer realm="hermes"'},
        )


# ---------------------------------------------------------------------------
# __main__ entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    setup_logging(json_format=get_settings().log_json)
    _settings = get_settings()
    uvicorn.run(
        "hermes.server:app",
        host=_settings.hermes_host,
        port=_settings.hermes_port,
    )
