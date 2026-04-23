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
    └──► Telemachy  (workflow engine)
```

**Subject schema:**
- Agent events: `hi.agents.{host}.{name}.{event}`
- Task events:  `hi.tasks.{team_id}.{task_id}.{event}`

## Configuration

| Variable       | Default               | Description                        |
|----------------|-----------------------|------------------------------------|
| NATS_URL       | nats://localhost:4222 | NATS server URL                    |
| HERMES_PORT    | 8080                  | Port Hermes listens on             |
| WEBHOOK_SECRET |                       | HMAC secret for webhook validation |

Configure external services to POST to `http://<hermes-host>:<HERMES_PORT>/webhook`.

## Key Principles

1. **Stateless HTTP layer** — Hermes holds no state; NATS JetStream is the event history.
2. **Fail fast on invalid payloads** — HMAC validation and Pydantic parsing reject bad data at the boundary.
3. **Subject granularity** — Fine-grained subjects let subscribers filter precisely.
4. **Async throughout** — FastAPI + nats-py are both async; never block the event loop.
5. **Config via environment** — All tunables come from env vars / `.env`; no hard-coded URLs.
6. **Pin dependency versions** — use `">=X.Y,<NEXT_MAJOR"` ranges in `pixi.toml`; never use `"*"`. Lower bound at the minor version level of the minimum supported version, upper bound at the next major to prevent breaking changes.

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
```
