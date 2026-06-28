# === Variables ===

NATS_URL    := env_var_or_default("NATS_URL", "nats://localhost:4222")
HERMES_PORT := env_var_or_default("HERMES_PORT", "8080")

# === Default ===

default:
    @just --list

# === Server ===

# Start the Hermes server (production mode)
start:
    pixi run python -m hermes

# Start with hot-reload for development
dev:
    pixi run uvicorn hermes.server:app --reload --port {{HERMES_PORT}}

# Check service health
health:
    curl -s http://localhost:{{HERMES_PORT}}/health | python3 -m json.tool

# === Setup ===

# One-command developer setup: copy .env, install deps, regenerate lock file
bootstrap:
    if [ ! -f .env ]; then cp .env.example .env; fi
    pixi install --frozen || pixi update
    @echo "Ready! Run 'just dev' to start."

# Install pre-commit hooks into the local git repo
setup-hooks:
    pixi run pre-commit install
    @echo "Pre-commit hooks installed."

# === Testing ===

# Run the full test suite (unit + integration)
test:
    pixi run pytest

# Run only unit tests (skip integration tests requiring NATS)
test-unit:
    pixi run pytest -m "not integration"

# Run only integration tests (requires a running NATS server)
test-integration:
    pixi run pytest -m integration

# Run tests and open HTML coverage report
test-cov:
    pixi run pytest --cov-report=html --cov-report=term-missing
    @echo "Coverage report: htmlcov/index.html"

# === Linting & Formatting ===

# Run ruff linter
lint:
    pixi run ruff check src tests

# Run ruff formatter
format:
    pixi run ruff format src tests

# Run mypy type checker
typecheck:
    pixi run mypy --strict src/hermes/

# === Docker ===

# Build the Docker image
docker-build tag="hermes:latest":
    docker build -t {{tag}} .

# Run the Docker container (requires NATS running separately)
docker-run tag="hermes:latest":
    docker run --rm \
        -p {{HERMES_PORT}}:8080 \
        -e NATS_URL={{NATS_URL}} \
        -e HERMES_PORT=8080 \
        -e WEBHOOK_SECRET="${WEBHOOK_SECRET:-}" \
        {{tag}}

# Start Hermes + NATS together via docker-compose
docker-up:
    docker compose up --build

# Stop and remove docker-compose containers
docker-down:
    docker compose down

# Run the read-only filesystem integration smoke test (boots docker-compose stack)
smoke-readonly-fs:
    bash scripts/smoke-readonly-fs.sh

# === Documentation ===

# Regenerate the committed OpenAPI spec (openapi.json) from the FastAPI app
export-openapi:
    pixi run python scripts/export-openapi.py

# === Security ===

# Audit dependencies for known vulnerabilities
audit:
    pixi run pip-audit

# Enforced in CI by the `deps/version-sync` job in .github/workflows/_required.yml (see #594, #496).
# Check that [project.dependencies] in pyproject.toml has upper bounds on all entries
dep-check:
    pixi run python scripts/check_dep_sync.py

# Warn when measured coverage exceeds per-module floors by >15pp (advisory)
check-coverage-floors:
    pixi run python scripts/check_coverage_floors.py

# Scan repository for leaked secrets (requires gitleaks binary)
scan-secrets:
    #!/usr/bin/env bash
    if ! command -v gitleaks &> /dev/null; then
        echo "Error: gitleaks not found. Install it with:"
        echo "  curl -sSfL https://github.com/gitleaks/gitleaks/releases/download/v8.21.2/gitleaks_8.21.2_linux_x64.tar.gz | tar -xz && sudo mv gitleaks /usr/local/bin/"
        exit 1
    fi
    gitleaks detect --source . --config .gitleaks.toml --verbose

# === NATS ===

# Start NATS server (uses Odysseus config if available, otherwise embedded defaults)
nats-start:
    #!/usr/bin/env bash
    set -euo pipefail
    ODYSSEUS_CONF="../Odysseus/configs/nats/server.conf"
    if [ -f "$ODYSSEUS_CONF" ]; then
        echo "Starting NATS with Odysseus config: $ODYSSEUS_CONF"
        nats-server -c "$ODYSSEUS_CONF" &
    else
        echo "Warning: Odysseus config not found at $ODYSSEUS_CONF — starting NATS with embedded defaults" >&2
        nats-server \
            --jetstream \
            --store_dir /tmp/nats-hermes \
            --port 4222 &
    fi
    echo "NATS started (PID $!)"
