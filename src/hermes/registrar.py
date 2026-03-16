"""Register ProjectHermes as a webhook receiver with ai-maestro."""

from __future__ import annotations

import asyncio
import logging
import sys

import httpx

from hermes.config import settings

logger = logging.getLogger(__name__)

# Events Hermes wants to receive from ai-maestro
_SUBSCRIBED_EVENTS: list[str] = [
    "agent.created",
    "agent.deleted",
    "agent.updated",
    "task.updated",
    "task.completed",
    "task.failed",
]


async def register_webhooks(
    maestro_url: str,
    hermes_url: str,
    api_key: str,
) -> None:
    """Call POST /api/webhooks on ai-maestro for each event type.

    Args:
        maestro_url: Base URL of the ai-maestro instance.
        hermes_url:  Publicly reachable URL of this Hermes instance.
        api_key:     Optional API key for ai-maestro authentication.
    """
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    endpoint = f"{maestro_url.rstrip('/')}/api/webhooks"

    payload = {
        "url": f"{hermes_url.rstrip('/')}/webhook",
        "events": _SUBSCRIBED_EVENTS,
        "secret": settings.webhook_secret,
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(endpoint, json=payload, headers=headers)
            response.raise_for_status()
            logger.info("Registered webhook for events %s → %s", _SUBSCRIBED_EVENTS, response.status_code)
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Failed to register webhooks: HTTP %s — %s",
                exc.response.status_code,
                exc.response.text,
            )
        except httpx.RequestError as exc:
            logger.error("Network error registering webhooks: %s", exc)


def main() -> None:
    """CLI entry point: register Hermes webhooks using settings from environment."""
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)

    hermes_url = f"http://localhost:{settings.hermes_port}"
    logger.info(
        "Registering webhooks: maestro=%s hermes=%s",
        settings.maestro_url,
        hermes_url,
    )

    asyncio.run(
        register_webhooks(
            maestro_url=settings.maestro_url,
            hermes_url=hermes_url,
            api_key=settings.maestro_api_key,
        )
    )


if __name__ == "__main__":
    main()
