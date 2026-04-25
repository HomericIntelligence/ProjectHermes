# SPDX-License-Identifier: MIT
"""Entry point for ``python -m hermes``."""

from __future__ import annotations

import argparse
import logging
import sys


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments, falling back to environment-variable defaults."""
    from hermes.config import get_settings

    settings = get_settings()

    parser = argparse.ArgumentParser(
        prog="hermes",
        description="ProjectHermes — bridges external webhooks to NATS JetStream.",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host interface to bind (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=settings.hermes_port,
        help=f"Port to listen on (default: {settings.hermes_port})",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error", "critical"],
        help="Logging level (default: info)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable hot-reload (development mode)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Configure logging and start the uvicorn server."""
    import uvicorn

    args = _parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    uvicorn.run(
        "hermes.server:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
