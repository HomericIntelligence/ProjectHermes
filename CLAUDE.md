# ProjectHermes — CLAUDE.md

## Project Overview

ProjectHermes is a lightweight Python service that bridges external webhooks to NATS JetStream.
It sits at the boundary of the HomericIntelligence mesh, translating HTTP webhook payloads
from external services (GitHub, Slack, third-party APIs) into durable, replayable NATS messages.

## Architecture

```
External Service (GitHub, Slack, etc.)
    │
    │  HTTP POST /webhook
    ▼
Hermes (FastAPI)
    │  validate HMAC signature
    │  parse event type + data
    │  route to NATS subject
    ▼
NATS JetStream (via ProjectKeystone)
    │  hi.agents.{host}.{name}.{event}
    │  hi.tasks.{team_id}.{task_id}.{event}
    │
    ├──► Argus      (observability)
    ├──► Agamemnon  (coordination)
    ├──► Telemachy  (workflow engine)
    │
    └──► homeric-deadletter (unknown event types)
```

**Subject schema:**
- Agent events: `hi.agents.{host}.{name}.{event}`
- Task events:  `hi.tasks.{team_id}.{task_id}.{event}`

**Subject slug limit:** Each token in a NATS subject (host, name, team\_id, task\_id, event) is
sanitised and truncated to **64 characters** (`_SLUG_MAX_LEN = 64` in `publisher.py`).
Values that exceed 64 characters are silently truncated before routing.

Unknown event types are routed to the `homeric-deadletter` NATS stream for inspection and replay.

## Configuration

| Variable              | Default                        | Description                                             |
|-----------------------|--------------------------------|---------------------------------------------------------|
| NATS_URL              | nats://localhost:4222          | NATS server URL                                         |
| HERMES_HOST           | 127.0.0.1                     | Host/IP the server binds to                             |
| HERMES_PORT           | 8080                           | Port Hermes listens on                                  |
| HERMES_PUBLIC_URL     | http://localhost:{HERMES_PORT} | Externally-reachable base URL for the /webhook endpoint |
| WEBHOOK_SECRET        |                                | HMAC secret for webhook validation (minimum 32 characters) |
| NATS_CONNECT_TIMEOUT  | 5.0                            | NATS connection timeout in seconds                      |
| NATS_PUBLISH_TIMEOUT  | 5.0                            | NATS publish timeout in seconds                         |
| AGAMEMNON_URL         |                                | Base URL of the Agamemnon coordination service          |
| AGAMEMNON_API_KEY     |                                | API key for authenticating with Agamemnon               |
| AGAMEMNON_TIMEOUT     | 10.0                           | Agamemnon API call timeout in seconds                   |
| SHUTDOWN_TIMEOUT      | 10.0                           | Graceful shutdown timeout in seconds                    |

Configure external services to POST to `http://<hermes-host>:<HERMES_PORT>/webhook`.

## Key Principles

1. **Stateless HTTP layer** — Hermes holds no state; NATS JetStream is the event history.
2. **Fail fast on invalid payloads** — HMAC validation and Pydantic parsing reject bad data at the boundary.
3. **Subject granularity** — Fine-grained subjects let subscribers filter precisely.
4. **Async throughout** — FastAPI + nats-py are both async; never block the event loop.
5. **Config via environment** — All tunables come from env vars / `.env`; no hard-coded URLs.
6. **Pin dependency versions** — use `">=X.Y,<NEXT_MAJOR"` ranges in `pixi.toml`; never use `"*"`. Lower bound at the minor version level of the minimum supported version, upper bound at the next major to prevent breaking changes.
7. **Subject sanitization** — NATS subject tokens are sanitized via `_slug()`: spaces become hyphens, dots become hyphens, and wildcards (`*` and `>`) are stripped entirely. Tokens are lowercased and subject strings are typically capped at 64 characters.

## Common Commands

```bash
just            # list all recipes
just start      # run production server
just dev        # hot-reload dev server
just test       # pytest
just lint       # ruff check src tests
just format     # ruff format src tests
just health     # curl /health endpoint
just nats-start # start NATS server

python -m hermes # alternative to 'just start' — runs the server directly
```
