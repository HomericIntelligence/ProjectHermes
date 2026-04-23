"""FastAPI application for ProjectHermes."""

from __future__ import annotations

import hashlib
import hmac
import logging
import sys
from contextlib import asynccontextmanager
from typing import Annotated, AsyncGenerator

from fastapi import Depends, FastAPI, HTTPException, Request, status

from hermes.config import Settings, get_settings
from hermes.models import WebhookPayload
from hermes.publisher import Publisher

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Connect to NATS on startup; disconnect cleanly on shutdown."""
    settings = get_settings()
    publisher = Publisher()
    try:
        await publisher.connect(settings.nats_url, connect_timeout=settings.nats_connect_timeout)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not connect to NATS at %s: %s", settings.nats_url, exc)

    app.state.publisher = publisher
    yield
    await publisher.disconnect()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="ProjectHermes",
    description="Bridges external webhooks to NATS JetStream.",
    version="0.1.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

SettingsDep = Annotated[Settings, Depends(get_settings)]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, object]:
    """Return service health and NATS connection status."""
    publisher: Publisher = app.state.publisher
    return {
        "status": "ok",
        "nats_connected": publisher.is_connected,
    }


@app.post("/webhook", status_code=status.HTTP_202_ACCEPTED)
async def receive_webhook(request: Request, settings: SettingsDep) -> dict[str, str]:
    """Receive an external webhook, validate its signature, and publish to NATS."""
    raw_body = await request.body()

    # HMAC validation (skipped when no secret is configured)
    if settings.webhook_secret:
        _verify_signature(raw_body, request.headers.get("X-Webhook-Signature", ""), settings)

    try:
        payload = WebhookPayload.model_validate_json(raw_body)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid payload: {exc}",
        ) from exc

    publisher: Publisher = app.state.publisher
    if not publisher.is_connected:
        logger.error("NATS not connected; cannot publish event %r", payload.event)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="NATS publisher not connected",
        )

    await publisher.publish(payload, publish_timeout=settings.nats_publish_timeout)
    return {"status": "accepted", "event": payload.event}


@app.get("/subjects")
async def list_subjects() -> dict[str, list[str]]:
    """Return the list of NATS subjects that have been published to."""
    publisher: Publisher = app.state.publisher
    return {"subjects": publisher.active_subjects}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _verify_signature(body: bytes, provided: str, settings: Settings) -> None:
    """Raise HTTP 401 if the HMAC-SHA256 signature does not match."""
    expected = hmac.new(
        settings.webhook_secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, provided):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )


# ---------------------------------------------------------------------------
# __main__ entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    _settings = get_settings()
    uvicorn.run(
        "hermes.server:app",
        host=_settings.hermes_host,
        port=_settings.hermes_port,
    )
