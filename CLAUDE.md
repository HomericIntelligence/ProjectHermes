# ProjectHermes — CLAUDE.md

## Project Overview

ProjectHermes is a lightweight Python service that bridges external webhooks to NATS JetStream.
It sits at the boundary of the HomericIntelligence mesh, translating HTTP webhook payloads
from external services (GitHub, Slack, third-party APIs) into durable, replayable NATS messages.

> **Multi-agent coordination:** See [AGENTS.md](AGENTS.md) for inter-service handoff protocols,
> agent roles, NATS subject schemas, and wire format details for AI agents operating in this mesh.

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
    ├──► Argus      (observability — NATS subscriber)
    ├──► Telemachy  (workflow engine — NATS subscriber)
    │
    └──► homeric-deadletter (unknown event types)

(Agamemnon is **not** a NATS subscriber — Hermes calls it directly via HTTP on
webhook receipt; see AGENTS.md §3.)
```

**Subject schema:**

- Agent events: `hi.agents.{host}.{name}.{event}`
- Task events:  `hi.tasks.{team_id}.{task_id}.{event}`

**Subject slug limit:** Each token in a NATS subject (host, name, team\_id, task\_id, event) is
sanitised and truncated to **64 characters** (`_SLUG_MAX_LEN = 64` in `publisher.py`).
Values that exceed 64 characters are silently truncated before routing.

Unknown event types are routed to the `homeric-deadletter` NATS stream for inspection and replay.

## Wire Format and schema_version

Every message published to NATS JetStream includes a `schema_version` integer field (from
`HermesEventBase` in `src/hermes/models.py`).  The current version is **1**.

| Field            | Type | Current value | Description                                         |
|------------------|------|---------------|-----------------------------------------------------|
| `schema_version` | int  | 1             | Wire format version; increments on breaking changes |

**Consumer guidance:**

- Always read `schema_version` before deserializing the `data` payload.
- If `schema_version > 1`, treat the message as a newer format your consumer may not understand;
  log a warning and skip (or route to a dead-letter queue) rather than crashing.
- `schema_version` will only be incremented on **breaking** changes to the wire format.  Additive
  changes (new optional fields) are backwards-compatible and do not require a version bump.
- There is no automated migration; consumers must handle old and new versions side-by-side during
  rolling deployments.

## Configuration

| Variable              | Default                        | Description                                             |
|-----------------------|--------------------------------|---------------------------------------------------------|
| NATS_URL              | nats://localhost:4222          | NATS server URL                                         |
| HERMES_HOST           | 127.0.0.1                     | Host/IP the server binds to                             |
| HERMES_PORT           | 8080                           | Port Hermes listens on                                  |
| HERMES_PUBLIC_URL     | `http://localhost:{HERMES_PORT}` | Externally-reachable base URL for the /webhook endpoint |
| WEBHOOK_SECRET        |                                | HMAC secret for webhook validation (minimum 32 characters) |
| MAX_PAYLOAD_BYTES     | 1048576                        | Maximum accepted request body size in bytes (1 MB)      |
| NATS_CONNECT_TIMEOUT  | 5.0                            | NATS connection timeout in seconds                      |
| NATS_PUBLISH_TIMEOUT  | 5.0                            | NATS publish timeout in seconds                         |
| NATS_RETRY_ATTEMPTS   | 3                              | Initial-connect retry attempts before giving up at startup |
| NATS_RETRY_INTERVAL   | 5.0                            | Seconds between initial-connect retries at startup (also surfaced via /health) |
| NATS_RECONNECT_INTERVAL | 5.0                          | Seconds between external reconnect attempts             |
| NATS_RECONNECT_HARD_TIMEOUT | 5.0                      | Per-attempt hard timeout for external reconnect (seconds) |
| DEAD_LETTER_API_KEY   |                                | API key for GET/DELETE /dead-letters (min 32 chars; auth bypassed when unset) |
| SHUTDOWN_TIMEOUT      | 10.0                           | Graceful shutdown timeout in seconds                    |
| WEBHOOK_RATE_LIMIT    | 60/minute                      | Rate limit for POST /webhook endpoint                   |
| WEBHOOK_RATE_LIMIT_KEY | ip                            | Rate-limit key strategy: `ip` (only currently wired) or `endpoint` (reserved) |
| SUBJECTS_RATE_LIMIT   | 60/minute                      | Rate limit for GET /subjects endpoint                   |
| ENABLE_DEAD_LETTER    | true                           | Route unparseable / unknown events to `hi.deadletter.>` |
| DEAD_LETTER_MAX_SIZE  | 1000                           | Maximum entries kept in the in-memory dead-letter queue |
| DEAD_LETTER_TTL_SECONDS | 86400                        | TTL applied to the JetStream `homeric-deadletter` stream (0 = no expiry) |
| DEAD_LETTER_ALERT_THRESHOLD | 0.8                      | Fraction of `DEAD_LETTER_MAX_SIZE` at which a queue-pressure alert is logged |
| DEAD_LETTER_PAGE_SIZE_DEFAULT | 100                    | Default page size for `GET /dead-letters`               |
| DEAD_LETTER_PAGE_SIZE_MAX | 500                        | Maximum allowed page size for `GET /dead-letters`       |

Configure external services to POST to `http://<hermes-host>:<HERMES_PORT>/webhook`.

> **Dead-letter eviction signal (#533):** At 100% of `DEAD_LETTER_MAX_SIZE` the
> in-memory deque evicts its oldest entry, logging a distinct WARNING and
> incrementing `hermes_dead_letter_evictions_total`. The durable JetStream copy
> is unaffected (no data loss).

<!-- -->

> **Security warning:** Setting `TLS_VERIFY=false` disables TLS certificate verification and MUST
> NOT be used in production.  When `TLS_VERIFY=false` is combined with `HERMES_HOST=0.0.0.0`
> (production binding), Hermes logs a loud `WARNING` at startup.  Always use a valid CA bundle
> (`TLS_CA_BUNDLE`) in production instead of disabling verification.

## Key Principles

1. **Stateless HTTP layer** — Hermes holds no state; NATS JetStream is the event history.
2. **Fail fast on invalid payloads** — HMAC validation and Pydantic parsing reject bad data at the boundary.
3. **Subject granularity** — Fine-grained subjects let subscribers filter precisely.
4. **Async throughout** — FastAPI + nats-py are both async; never block the event loop.
5. **Config via environment** — All tunables come from env vars / `.env`; no hard-coded URLs.
6. **Pin dependency versions** — use `">=X.Y,<NEXT_MAJOR"` ranges in `pixi.toml`; never use `"*"`.
   Lower bound at the minor version level of the minimum supported version, upper bound at the next
   major to prevent breaking changes.
7. **Subject sanitization** — NATS subject tokens are sanitized via `_slug()`: spaces become
   hyphens, dots become hyphens, and wildcards (`*` and `>`) are stripped entirely. Tokens are
   lowercased and subject strings are typically capped at 64 characters.

## Logging

`setup_logging()` (in `src/hermes/logging_config.py`) configures the root logger
and writes to `sys.stdout` by default, matching the pre-#328
`logging.basicConfig(stream=sys.stdout)` behaviour that downstream pipelines and
log collectors depend on. Callers that need stderr can pass
`stream=sys.stderr` explicitly. See issue #462.

## Future Integrations

### Agamemnon (deferred — fields removed per #324)

`agamemnon_url` / `agamemnon_api_key` were intentionally removed as dead config (YAGNI). When
Agamemnon integration is implemented, follow this pattern so it matches existing patterns in the
codebase:

- Add fields to `Settings` in `hermes/config.py`: `agamemnon_url: str | None = None`,
  `agamemnon_api_key: SecretStr | None = None`, plus a `agamemnon_timeout: float = 5.0`
  (validate `> 0`). Leaving the URL unset must keep Agamemnon dispatch fully disabled.
- Hold a single `httpx.AsyncClient` for the lifetime of the app (constructed in `lifespan`,
  closed in shutdown) — never construct per-request. Pass the connection-holding client through
  `app.state` or a DI provider.
- Plumb `agamemnon_timeout` into the client (`httpx.AsyncClient(timeout=...)`) rather than
  hard-coding a value at the call site.
- Surface health on `/health` (e.g. `agamemnon_reachable: bool`) only when `agamemnon_url` is
  configured — do not regress the no-Agamemnon happy path.
- Authenticate via `Authorization: Bearer ${AGAMEMNON_API_KEY}`. Never log the key.

If the design evolves significantly, capture the decision in a new ADR rather than changing this
note.

## Container Runtime Requirements

The Hermes container is configured `read_only: true` (see `docker-compose.yml`). Operators using
plain `docker run` instead of compose **must** supply the same hardening flags or the container
will fail to start when it tries to write logs/temp files:

```bash
docker run --rm \
  --read-only \
  --tmpfs /tmp \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  -p 8085:8085 \
  -e NATS_URL=nats://host:4222 \
  ghcr.io/homericintelligence/projecthermes:<tag>
```

The `--tmpfs /tmp` mount is required by the Python interpreter and Uvicorn for ephemeral state.

## CI/CD

Docker images are published to GHCR (`ghcr.io/<org>/projecthermes`) **only on version tags**
matching `v*.*.*` and via manual `workflow_dispatch`. Merges to `main` do **not** trigger a
publish. To release a new image, push a semver tag (e.g.
`git tag v1.2.3 && git push origin v1.2.3`).

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
