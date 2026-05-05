#!/usr/bin/env python
# SPDX-License-Identifier: MIT
"""Export the Hermes FastAPI app's OpenAPI schema to a JSON file.

Usage:
    python scripts/export-openapi.py [--output openapi.json]

This script imports the FastAPI app object without starting the NATS lifespan,
so it is safe to run without a running NATS server.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from hermes.server import app  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Hermes OpenAPI spec to JSON")
    parser.add_argument(
        "--output",
        default="openapi.json",
        help="Output file path (default: openapi.json)",
    )
    args = parser.parse_args()

    spec = app.openapi()

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=2)
        f.write("\n")

    print(f"OpenAPI spec written to {args.output}")


if __name__ == "__main__":
    main()
