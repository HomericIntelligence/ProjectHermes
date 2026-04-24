"""FastAPI application for ProjectHermes."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import signal
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware

from hermes import __version__
from hermes.config import Settings, get_settings
from hermes.logging_config import setup_logging
from hermes.metrics import WEBHOOKS_FAILED, WEBHOOKS_RECEIVED
from hermes.models import (
    ErrorResponse,
    HealthResponse,
    SubjectsResponse,
    WebhookAcceptedResponse,
    WebhookPayload,
)
from hermes.publisher import AGENT_EVENTS, TASK_EVENTS, Publisher

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shutdown state — module-level so tests can inspect/mutate directly
# ---------------------------------------------------------------------------

_shutdown_event: asyncio.Event = asyncio.Event()
_inflight: int = 0
_inflight_lock: asyncio.Lock = asyncio.Lock()

_NATS_CONNECT_TIMEOUT = 5
_NATS_RETRY_ATTEMPTS = 3
_NATS_RETRY_INTERVAL = 5

_REQUEST_ID_HEADER = "X-Request-ID"

# ---------------------------------------------------------------------------
# Application lifespan
# ---------------------------------------------------------------------------


def _on_shutdown_signal(sig: signal.Signals) -> None:
    """Set the module-level shutdown event when a termination signal arrives."""
    logger.info("Received signal %s; initiating graceful shutdown", sig.name)
    _shutdown_event.set()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Connect to NATS on startup with retries; abort startup if all attempts fail."""
    global _shutdown_event, _inflight

    settings = get_settings()
    publisher = Publisher(enable_dead_letter=settings.enable_dead_letter)
    last_exc: Exception | None = None
    for attempt in range(1, _NATS_RETRY_ATTEMPTS + 1):
        try:
            await asyncio.wait_for(
                publisher.connect(settings.nats_url, connect_timeout=settings.nats_connect_timeout),
                timeout=_NATS_CONNECT_TIMEOUT,
            )
            last_exc = None
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.error(
                "NATS connect attempt %d/%d failed (%s: %s); retrying in %ds",
                attempt,
                _NATS_RETRY_ATTEMPTS,
                type(exc).__name__,
                exc,
                _NATS_RETRY_INTERVAL,
            )
            if attempt < _NATS_RETRY_ATTEMPTS:
                await asyncio.sleep(_NATS_RETRY_INTERVAL)

    if last_exc is not None:
        logger.critical(
            "Could not connect to NATS at %s after %d attempts; aborting startup",
            settings.nats_url,
            _NATS_RETRY_ATTEMPTS,
        )
        raise last_exc

    # Install signal handlers so SIGTERM/SIGINT trigger graceful shutdown
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _on_shutdown_signal, sig)

    _shutdown_event = asyncio.Event()
    _inflight = 0

    setup_logging(json_format=settings.log_json)

    if not settings.webhook_secret:
        logger.warning(
            "HMAC webhook validation is DISABLED — set WEBHOOK_SECRET to enable signature verification",
            extra={"hmac_enabled": False},
        )

    _log_startup_banner(publisher, settings)
    app.state.publisher = publisher
    yield

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
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    else:
        logger.warning("Shutdown: timed out waiting for in-flight requests; proceeding")

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

# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

SettingsDep = Annotated[Settings, Depends(get_settings)]


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Assign a unique request ID to every incoming request.

    Reads ``X-Request-ID`` from the incoming headers if present (pass-through),
    otherwise generates a fresh UUID4. The ID is stored on ``request.state``
    so route handlers can include it in NATS payloads and log messages, and is
    echoed back to the caller via the ``X-Request-ID`` response header.
    """

    async def dispatch(self, request: Request, call_next: object) -> Response:
        request_id = request.headers.get(_REQUEST_ID_HEADER) or str(uuid.uuid4())
        request.state.request_id = request_id
        response: Response = await call_next(request)  # type: ignore[operator]
        response.headers[_REQUEST_ID_HEADER] = request_id
        return response


class ShutdownMiddleware(BaseHTTPMiddleware):
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
    return HealthResponse(
        status="ok" if connected else "degraded",
        nats_connected=connected,
        shutting_down=_shutdown_event.is_set(),
        hmac_validation_enabled=bool(cfg.webhook_secret),
        hermes_public_url=cfg.hermes_public_url,
    )


@app.get("/ready", responses={503: {"model": ErrorResponse, "description": "NATS not connected"}})
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
        503: {"model": ErrorResponse, "description": "NATS publisher not connected"},
    },
)
async def receive_webhook(request: Request, settings: SettingsDep) -> WebhookAcceptedResponse:
    """Receive an external webhook, validate its signature, and publish to NATS."""
    raw_body = await request.body()
    request_id: str = request.state.request_id

    # HMAC validation (skipped when no secret is configured)
    if settings.webhook_secret:
        _verify_signature(raw_body, request.headers.get("X-Webhook-Signature", ""), settings)

    try:
        payload = WebhookPayload.model_validate_json(raw_body)
    except Exception as exc:
        logger.warning("Invalid webhook payload: %s", exc)
        WEBHOOKS_FAILED.labels(reason="invalid_payload").inc()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid payload format",
        ) from exc

    WEBHOOKS_RECEIVED.labels(event_type=payload.event).inc()

    publisher: Publisher = app.state.publisher
    if not publisher.is_connected:
        logger.error(
            "NATS not connected; cannot publish event %r (request_id=%s)",
            payload.event,
            request_id,
        )
        WEBHOOKS_FAILED.labels(reason="nats_not_connected").inc()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="NATS publisher not connected",
        )

    await publisher.publish(payload, publish_timeout=settings.nats_publish_timeout, request_id=request_id)
    return WebhookAcceptedResponse(status="accepted", event=payload.event)


@app.get(
    "/subjects",
    response_model=SubjectsResponse,
)
async def list_subjects() -> SubjectsResponse:
    """Return the list of NATS subjects that have been published to."""
    publisher: Publisher = app.state.publisher
    return SubjectsResponse(subjects=publisher.active_subjects)


@app.get("/metrics")
async def metrics() -> Response:
    """Expose Prometheus metrics in text format."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/dead-letters")
async def dead_letters() -> dict[str, list[dict[str, Any]]]:
    """Return the in-memory dead-letter queue of unroutable webhook events."""
    publisher: Publisher = app.state.publisher
    return {"dead_letters": publisher.dead_letters}


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
