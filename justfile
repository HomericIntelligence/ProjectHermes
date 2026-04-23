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

# === Integration ===

# Point external services at POST /webhook directly
register-webhook:
    bash scripts/register-webhooks.sh

# === Testing ===

# Run the test suite
test:
    pixi run pytest

# === Linting & Formatting ===

# Run ruff linter
lint:
    pixi run ruff check src tests

# Run ruff formatter
format:
    pixi run ruff format src tests

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
        echo "Odysseus config not found; starting NATS with embedded defaults"
        nats-server \
            --jetstream \
            --store_dir /tmp/nats-hermes \
            --port 4222 &
    fi
    echo "NATS started (PID $!)"
