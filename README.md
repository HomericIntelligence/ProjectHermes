# ProjectHermes

ProjectHermes bridges external webhooks (GitHub, Slack, third-party APIs) to
[NATS JetStream](https://docs.nats.io/nats-concepts/jetstream) for pub/sub fan-out and event
replay across the HomericIntelligence ecosystem.

## Purpose

External services fire HTTP webhooks when events occur. Hermes receives those webhooks, validates
them, and publishes structured messages to NATS subjects. Downstream services (Argus, Agamemnon,
Telemachy) subscribe to relevant subjects and react accordingly. JetStream provides durable
storage so late-joining subscribers can replay missed events.

```
External Service ──HTTP POST /webhook──► Hermes ──publish──► NATS JetStream
                                                                    │
                              ┌─────────────────────────────────────┤
                              ▼               ▼                     ▼
                            Argus         Agamemnon             Telemachy
```

## Quick Start

```bash
# Start NATS (if not already running via Odysseus)
just nats-start

# Start Hermes
just start

# Check health
just health
```

## Integration

External services (GitHub, Slack, etc.) should configure their webhooks to POST directly to Hermes:

```
POST http://<hermes-host>:<HERMES_PORT>/webhook
```

Hermes validates the HMAC signature (`WEBHOOK_SECRET`) and publishes the event to NATS JetStream.

## Subject Schema

### Agent Events

```
hi.agents.{host}.{name}.{event}
```

| Token  | Description                              |
|--------|------------------------------------------|
| host   | Hostname of the agent's Docker host      |
| name   | Agent container/service name             |
| event  | created, updated, deleted                |

Example: `hi.agents.docker-desktop.researcher.created`

### Task Events

> **Note:** Task webhook events (`task.updated`, `task.completed`, `task.failed`) are defined as
> forward-compatible scaffolding but are **not yet emitted** by the upstream service.
> See [Odysseus#33](https://github.com/HomericIntelligence/Odysseus/issues/33) for the tracking
> issue.

```
hi.tasks.{team_id}.{task_id}.{event}
```

| Token   | Description                             |
|---------|-----------------------------------------|
| team_id | Team identifier                         |
| task_id | Unique task identifier                  |
| event   | updated, completed, failed              |

Example: `hi.tasks.team-alpha.task-42.updated`

## Wire Format and schema_version

All NATS messages published by Hermes include a `schema_version` integer field.

| Field            | Current value | Description                                         |
|------------------|---------------|-----------------------------------------------------|
| `schema_version` | 1             | Wire format version; increments on breaking changes |

**Consumer guidance:**

- Check `schema_version` before deserializing the `data` payload.
- If `schema_version` is higher than your consumer knows about, log a warning and skip or
  dead-letter the message rather than crashing.
- Additive changes (new optional fields) are backwards-compatible and do not bump the version.
- There is no automated migration; consumers must handle multiple versions side-by-side during
  rolling deployments.

## Endpoints

Hermes exposes several endpoints for webhooks, health checks, and observability:

| Endpoint         | Method | Description                                                                         | Status Code     |
|------------------|--------|-------------------------------------------------------------------------------------|-----------------|
| `/webhook`       | POST   | Accept and validate incoming webhooks; publishes to NATS                            | 202 / 401 / 422 |
| `/health`        | GET    | Service health check; returns NATS connection status                                | 200 / 503       |
| `/ready`         | GET    | Readiness probe for orchestrators (Kubernetes, Docker Compose)                      | 200 / 503       |
| `/metrics`       | GET    | Prometheus metrics (counter, gauge, histogram)                                      | 200             |
| `/subjects`      | GET    | List all NATS subjects published to in this session                                 | 200             |
| `/events`        | GET    | Canonical list of supported webhook event types (agent_events, task_events)         | 200             |
| `/dead-letters`  | GET    | View in-memory dead-letter queue of unroutable events                               | 200             |

### Health Checks

- **`/health`** returns `200 OK` when NATS is connected, `503 Service Unavailable` when
  disconnected. Use this for liveness probes and metrics scraping (e.g., Prometheus).
- **`/ready`** returns `200 OK` when ready to accept webhooks (NATS connected),
  `503 Service Unavailable` otherwise. Use for Kubernetes readiness probes.

## API Documentation

The committed OpenAPI 3.x specification is at [`openapi.json`](openapi.json) in the repository
root. It is generated from the live FastAPI app and kept in sync with each release.

To regenerate it locally:

```bash
just export-openapi
```

FastAPI also serves interactive docs at runtime:

- Swagger UI: `http://localhost:<HERMES_PORT>/docs`
- ReDoc: `http://localhost:<HERMES_PORT>/redoc`

## Architecture Decisions

Hermes-specific architectural decisions are documented as ADRs in [`docs/adr/`](docs/adr/).

| ADR                                                    | Decision                                                                              |
|--------------------------------------------------------|---------------------------------------------------------------------------------------|
| [ADR-001](docs/adr/ADR-001-nats-reconnect-strategy.md) | `allow_reconnect=False` — fail fast on NATS disconnect rather than buffering silently |
| [ADR-002](docs/adr/ADR-002-dead-letter-strategy.md)    | Route unknown event types to `hi.deadletter.<event>` JetStream subject                |
| [ADR-003](docs/adr/ADR-003-schema-version-field.md)    | Include `schema_version` integer in every wire-format message                         |

## Configuration

Copy `.env.example` to `.env` and fill in values:

```bash
cp .env.example .env
```

### Environment Variables

| Variable              | Default                   | Description                                                 |
|-----------------------|---------------------------|-------------------------------------------------------------|
| NATS_URL              | nats://localhost:4222     | NATS server URL                                             |
| NATS_CONNECT_TIMEOUT  | 5.0                       | Seconds to wait for initial NATS connection                 |
| NATS_PUBLISH_TIMEOUT  | 5.0                       | Seconds to wait for publish confirmation                    |
| HERMES_HOST           | 127.0.0.1                 | Host/IP to bind (use 0.0.0.0 in Docker)                     |
| HERMES_PORT           | 8080                      | Port Hermes listens on                                      |
| HERMES_PUBLIC_URL     | `http://localhost:{port}` | Externally-reachable base URL for the /webhook endpoint     |
| WEBHOOK_SECRET        |                           | HMAC secret for webhook validation (min 32 chars)           |
| WEBHOOK_RATE_LIMIT    | 60/minute                 | Rate limit per IP (e.g., 60/minute, 100/hour)               |
| MAX_PAYLOAD_BYTES     | 1048576                   | Max webhook payload size in bytes (1 MiB default)           |
| ACTIVE_SUBJECTS_MAX   | 1000                      | Max distinct NATS subjects to track in memory               |
| LOG_JSON              | false                     | Enable JSON-formatted logs for structured logging           |
| ENABLE_DEAD_LETTER    | true                      | Store unparseable events to `hi.deadletter.>` stream        |
| AGAMEMNON_TIMEOUT     | 10.0                      | HTTP timeout for Agamemnon requests in seconds              |
| SHUTDOWN_TIMEOUT      | 10.0                      | Grace period for graceful shutdown in seconds               |
| TLS_CA_BUNDLE         |                           | Path to CA certificate bundle (PEM)                         |
| TLS_CERT_FILE         |                           | Path to client TLS certificate (PEM) for mTLS               |
| TLS_KEY_FILE          |                           | Path to client TLS private key (PEM)                        |
| TLS_VERIFY            | true                      | Verify TLS certificates (set false only for dev/testing)    |

> **Security:** If `WEBHOOK_SECRET` is empty, HMAC validation is **disabled** and a warning is
> logged at startup. Always set a secret in production.
>
> **Security:** `TLS_VERIFY=false` disables TLS certificate verification and **must never be used
> in production**. When this option is set alongside `HERMES_HOST=0.0.0.0` (the production
> binding), Hermes logs a loud `WARNING` at startup. Use `TLS_CA_BUNDLE` to supply a trusted CA
> instead.

### NATS Reconnection

Hermes connects to NATS with the following behavior:

- **Initial connection:** Uses `NATS_CONNECT_TIMEOUT` to establish the first connection to the
  NATS server.
- **Automatic reconnection:** When an established connection drops, nats-py automatically attempts
  to reconnect according to its internal backoff strategy.
- **Publish timeout:** Messages published to JetStream have a per-operation timeout of
  `NATS_PUBLISH_TIMEOUT`, ensuring pub/sub calls don't hang indefinitely.
- **Connection lifecycle:** Use `SHUTDOWN_TIMEOUT` to allow graceful in-flight request completion
  before forced shutdown.

If connection issues occur, check:

1. NATS server is reachable at `NATS_URL`
2. Network connectivity and firewall rules
3. TLS configuration if using `tls://` scheme
4. Application logs for specific error messages

## TLS Configuration

For production deployments, Hermes can be configured to use TLS for NATS connections.
Set the following environment variables:

| Variable       | Description                                                            |
|----------------|------------------------------------------------------------------------|
| TLS_CA_BUNDLE  | Path to CA certificate bundle file (for server certificate validation) |
| TLS_CERT_FILE  | Path to client certificate file (for mTLS)                             |
| TLS_KEY_FILE   | Path to client private key file (for mTLS)                             |
| TLS_VERIFY     | Enable/disable hostname verification (default: true)                   |

Example with mTLS (mutual TLS):

```bash
export NATS_URL=tls://nats.example.com:4222
export TLS_CERT_FILE=/etc/hermes/certs/client-cert.pem
export TLS_KEY_FILE=/etc/hermes/certs/client-key.pem
export TLS_CA_BUNDLE=/etc/hermes/certs/ca-bundle.crt
just start
```

## Development

```bash
just bootstrap  # install deps and git hooks (first-time setup)
just dev        # hot-reload dev server
just test       # run tests
just lint       # ruff check
just format     # ruff format
```

## Multi-Agent Coordination

For AI agents producing or consuming Hermes events, see [AGENTS.md](AGENTS.md). It covers the
HTTP inbound interface, NATS subject patterns, wire format, downstream consumer roles, and coding
conventions for agents working in this repository.

## Security

To report a vulnerability, see [SECURITY.md](SECURITY.md). Do not open public issues for security vulnerabilities.

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).
