#!/usr/bin/env python
# SPDX-License-Identifier: MIT
"""Export the Hermes FastAPI app's OpenAPI schema to a JSON or YAML file.

Usage:
    python scripts/export-openapi.py [--output openapi.json] [--format json|yaml]

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
    parser = argparse.ArgumentParser(description="Export Hermes OpenAPI spec to JSON or YAML")
    parser.add_argument(
        "--output",
        default=None,
        help="Output file path (default: openapi.json or openapi.yaml depending on --format)",
    )
    parser.add_argument(
        "--format",
        choices=("json", "yaml"),
        default="json",
        help=(
            "Output format. 'yaml' emits PyYAML-formatted output for "
            "OpenAPI tooling that prefers YAML (Stoplight, Redocly, Spectral). "
            "See HomericIntelligence/ProjectHermes#433."
        ),
    )
    args = parser.parse_args()

    output = args.output or ("openapi.yaml" if args.format == "yaml" else "openapi.json")

    spec = app.openapi()

    if args.format == "yaml":
        import yaml  # type: ignore[import-untyped]  # PyYAML — transitive dep; no stubs.

        with open(output, "w", encoding="utf-8") as f:
            # sort_keys=True keeps output deterministic across FastAPI / Python
            # dict-insertion-order changes; required by the openapi-drift CI check (#431).
            yaml.safe_dump(spec, f, sort_keys=True)
    else:
        with open(output, "w", encoding="utf-8") as f:
            # sort_keys=True keeps output deterministic across FastAPI / Python
            # dict-insertion-order changes; required by the openapi-drift CI check (#431).
            json.dump(spec, f, indent=2, sort_keys=True)
            f.write("\n")

    print(f"OpenAPI spec written to {output}")


if __name__ == "__main__":
    main()
