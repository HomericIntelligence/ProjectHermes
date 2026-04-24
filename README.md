# ProjectHermes

ProjectHermes bridges external webhooks (GitHub, Slack, third-party APIs) to [NATS JetStream](https://docs.nats.io/nats-concepts/jetstream) for pub/sub fan-out and event replay across the HomericIntelligence ecosystem.

## Purpose

External services fire HTTP webhooks when events occur. Hermes receives those webhooks, validates them, and publishes structured messages to NATS subjects. Downstream services (Argus, Agamemnon, Telemachy) subscribe to relevant subjects and react accordingly. JetStream provides durable storage so late-joining subscribers can replay missed events.

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

> **Note:** Task webhook events (`task.updated`, `task.completed`, `task.failed`) are defined as forward-compatible scaffolding but are **not yet emitted** by the upstream service. See [Odysseus#33](https://github.com/HomericIntelligence/Odysseus/issues/33) for the tracking issue.

```
hi.tasks.{team_id}.{task_id}.{event}
```

| Token   | Description                             |
|---------|-----------------------------------------|
| team_id | Team identifier                         |
| task_id | Unique task identifier                  |
| event   | updated, completed, failed              |

Example: `hi.tasks.team-alpha.task-42.updated`

## Configuration

Copy `.env.example` to `.env` and fill in values:

```bash
cp .env.example .env
```

### Environment Variables

| Variable              | Default          | Description                                                 |
|-----------------------|------------------|-------------------------------------------------------------|
| NATS_URL              | nats://localhost:4222 | NATS server URL                                         |
| NATS_CONNECT_TIMEOUT  | 5.0              | Seconds to wait for initial NATS connection                 |
| NATS_PUBLISH_TIMEOUT  | 5.0              | Seconds to wait for publish confirmation                    |
| HERMES_HOST           | 127.0.0.1        | Host/IP to bind (use 0.0.0.0 in Docker)                   |
| HERMES_PORT           | 8080             | Port Hermes listens on                                      |
| HERMES_PUBLIC_URL     | http://localhost:{port} | Externally-reachable base URL for the /webhook endpoint |
| WEBHOOK_SECRET        |                  | HMAC secret for webhook validation (min 32 chars)           |
| WEBHOOK_RATE_LIMIT    | 60/minute        | Rate limit per IP (e.g., 60/minute, 100/hour)              |
| MAX_PAYLOAD_BYTES     | 1048576          | Max webhook payload size in bytes (1 MiB default)           |
| ACTIVE_SUBJECTS_MAX   | 1000             | Max distinct NATS subjects to track in memory               |
| LOG_JSON              | false            | Enable JSON-formatted logs for structured logging           |
| ENABLE_DEAD_LETTER    | true             | Store unparseable events to `hi.deadletter.>` stream        |
| AGAMEMNON_TIMEOUT     | 10.0             | HTTP timeout for Agamemnon requests in seconds              |
| SHUTDOWN_TIMEOUT      | 10.0             | Grace period for graceful shutdown in seconds               |
| TLS_CA_BUNDLE         |                  | Path to CA certificate bundle (PEM)                         |
| TLS_CERT_FILE         |                  | Path to client TLS certificate (PEM) for mTLS               |
| TLS_KEY_FILE          |                  | Path to client TLS private key (PEM)                        |
| TLS_VERIFY            | true             | Verify TLS certificates (set false only for dev/testing)    |

> **Security:** If `WEBHOOK_SECRET` is empty, HMAC validation is **disabled** and a warning is logged at startup. Always set a secret in production.

### NATS Reconnection

Hermes connects to NATS with the following behavior:

- **Initial connection:** Uses `NATS_CONNECT_TIMEOUT` to establish the first connection to the NATS server.
- **Automatic reconnection:** When an established connection drops, nats-py automatically attempts to reconnect according to its internal backoff strategy.
- **Publish timeout:** Messages published to JetStream have a per-operation timeout of `NATS_PUBLISH_TIMEOUT`, ensuring pub/sub calls don't hang indefinitely.
- **Connection lifecycle:** Use `SHUTDOWN_TIMEOUT` to allow graceful in-flight request completion before forced shutdown.

If connection issues occur, check:
1. NATS server is reachable at `NATS_URL`
2. Network connectivity and firewall rules
3. TLS configuration if using `tls://` scheme
4. Application logs for specific error messages

## Development

```bash
just bootstrap  # install deps and git hooks (first-time setup)
just dev        # hot-reload dev server
just test       # run tests
just lint       # ruff check
just format     # ruff format
```

## Security

To report a vulnerability, see [SECURITY.md](SECURITY.md). Do not open public issues for security vulnerabilities.

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).
