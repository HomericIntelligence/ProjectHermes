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

| Variable       | Default               | Description                        |
|----------------|-----------------------|------------------------------------|
| NATS_URL       | nats://localhost:4222 | NATS server URL                    |
| HERMES_PORT    | 8080                  | Port Hermes listens on             |
| WEBHOOK_SECRET |                       | HMAC secret for webhook validation |

## Development

```bash
just dev      # hot-reload dev server
just test     # run tests
just lint     # ruff check
just format   # ruff format
```

## Security

To report a vulnerability, see [SECURITY.md](SECURITY.md). Do not open public issues for security vulnerabilities.

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).
