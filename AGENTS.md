# AGENTS.md — HomericIntelligence Multi-Agent Coordination

This document describes how AI agents interact with **ProjectHermes** — the primary HTTP-to-NATS
bridge in the HomericIntelligence mesh. It covers three audiences:

1. **Event producers** — agents that POST webhook events to Hermes
2. **Event consumers** — agents that subscribe to NATS JetStream subjects published by Hermes
3. **Coding agents** — AI agents working inside this repository

---

## Service Identity

| Property          | Value                              |
|-------------------|------------------------------------|
| Service name      | `hermes`                           |
| Default port      | `8080` (env: `HERMES_PORT`)        |
| Default bind      | `127.0.0.1` (env: `HERMES_HOST`)   |
| Production bind   | `0.0.0.0`                          |
| Docker image      | `hermes` (see `docker-compose.yml`)|

---

## Role in the Mesh

```
External Service (GitHub, Slack, etc.)
    │
    │  HTTP POST /webhook
    ▼
Hermes (FastAPI — this service)
    │  validate HMAC signature
    │  parse event type + data
    │  route to NATS subject
    ▼
NATS JetStream
    │
    ├──► hi.agents.>     → Argus (observability)
    ├──► hi.tasks.>      → Telemachy (workflow engine)
    └──► hi.deadletter.> → unknown / unroutable events

Hermes also issues a synchronous HTTP call to Agamemnon (out-of-band from NATS)
on webhook receipt — see §3 below.
```

---

## §1 — Event Producers (agents POSTing to Hermes)

### Endpoint

```
POST {HERMES_PUBLIC_URL}/webhook
```

`HERMES_PUBLIC_URL` defaults to `http://localhost:{HERMES_PORT}`.

### Request Schema

```json
{
  "event":     "<event-type>",
  "data":      { "<arbitrary key-value pairs>" },
  "timestamp": "<ISO 8601 with timezone, e.g. 2026-05-05T12:00:00Z>",
  "signature": "<optional HMAC-SHA256 hex digest>"
}
```

| Field       | Type            | Required | Notes                                                       |
|-------------|-----------------|----------|-------------------------------------------------------------|
| `event`     | string          | yes      | Must be a recognised event type (see below)                 |
| `data`      | object          | yes      | Arbitrary payload; routing fields pulled from here          |
| `timestamp` | ISO 8601 string | yes      | Must include timezone offset; naive timestamps rejected     |
| `signature` | string          | no       | HMAC-SHA256 hex digest; required if `WEBHOOK_SECRET` is set |

### HMAC Validation

When `WEBHOOK_SECRET` is configured (minimum 32 characters), Hermes validates the
`X-Hub-Signature-256` request header. The expected format is `sha256=<hex-digest>` computed over
the raw request body. Requests with an invalid or missing signature are rejected with `401
Unauthorized`.

### Accepted Event Types

| Event           | NATS stream       | Subject pattern                            |
|-----------------|-------------------|--------------------------------------------|
| `agent.created` | `homeric-agents`  | `hi.agents.{host}.{name}.created`          |
| `agent.updated` | `homeric-agents`  | `hi.agents.{host}.{name}.updated`          |
| `agent.deleted` | `homeric-agents`  | `hi.agents.{host}.{name}.deleted`          |
| `task.updated`  | `homeric-tasks`   | `hi.tasks.{team_id}.{task_id}.updated`     |
| `task.completed`| `homeric-tasks`   | `hi.tasks.{team_id}.{task_id}.completed`   |
| `task.failed`   | `homeric-tasks`   | `hi.tasks.{team_id}.{task_id}.failed`      |

> **Forward-compatible only:** `task.updated`, `task.completed`, and `task.failed` are accepted by
> Hermes for forward-compatibility, but the upstream service does **not yet emit them**. Consumers
> should not rely on receiving these events today. See [§3 — Not Yet Integrated](#-3--downstream-consumers)
> and [Odysseus#33](https://github.com/HomericIntelligence/Odysseus/issues/33).

Unknown event types are routed to `hi.deadletter.{sanitized-event}` when
`ENABLE_DEAD_LETTER=true` (the default).

### Data Fields Used for Subject Routing

**Agent events** — Hermes reads these fields from `data` to build the NATS subject:

| Field           | Aliases       | Maps to subject token |
|-----------------|---------------|-----------------------|
| `hostId`        | `host`        | `{host}`              |
| `name`          | —             | `{name}`              |

**Task events** — Hermes reads these fields from `data`:

| Field     | Aliases    | Maps to subject token |
|-----------|------------|-----------------------|
| `teamId`  | `team_id`  | `{team_id}`           |
| `id`      | `task_id`  | `{task_id}`           |

Missing routing fields fall back to the literal token `unknown` — messages are never silently
dropped.

### Successful Response

HTTP `202 Accepted`:

```json
{ "status": "accepted", "event": "<event-type>" }
```

### Rate Limiting

Default: **60 requests/minute** per IP address (`WEBHOOK_RATE_LIMIT`). Exceed this and Hermes
returns `429 Too Many Requests`.

### Payload Size Limit

Default: **1 MiB** (`MAX_PAYLOAD_BYTES`). Larger bodies are rejected with `413 Request Entity Too
Large`.

---

## §2 — Event Consumers (agents subscribing to NATS)

### JetStream Streams

| Stream name          | Subject filter     | Durable storage |
|----------------------|--------------------|-----------------|
| `homeric-agents`     | `hi.agents.>`      | yes             |
| `homeric-tasks`      | `hi.tasks.>`       | yes             |
| `homeric-deadletter` | `hi.deadletter.>`  | yes             |

Streams are created automatically by Hermes on startup if they do not already exist.

### Subject Patterns

```
hi.agents.{host}.{name}.{verb}
hi.tasks.{team_id}.{task_id}.{verb}
hi.deadletter.{sanitized-event}
```

Each `{token}` in a subject is sanitised by `_slug()` in `publisher.py`:

- Spaces → `-`
- Dots → `-`
- `*` and `>` stripped entirely
- Lowercased
- Truncated to **64 characters**

### Wire Format

Every NATS message body is a UTF-8 JSON object:

```json
{
  "schema_version": 1,
  "event":          "<event-type>",
  "data":           { "<original webhook data>" },
  "timestamp":      "<ISO 8601 with timezone>",
  "request_id":     "<correlation ID or empty string>"
}
```

| Field            | Type   | Notes                                                   |
|------------------|--------|---------------------------------------------------------|
| `schema_version` | int    | Current value: `1`. Check before deserializing `data`.  |
| `event`          | string | Original event type string from the webhook             |
| `data`           | object | Original webhook `data` payload verbatim                |
| `timestamp`      | string | Timezone-aware ISO 8601                                 |
| `request_id`     | string | HTTP request correlation ID; empty string if absent     |

### Consumer Guidance

- **Always read `schema_version` first.** If `schema_version > 1`, your consumer may not
  understand the format; log a warning and route to a dead-letter queue rather than crashing.
- Additive changes (new optional fields) do **not** bump `schema_version`; code defensively.
- There is no automated migration. Handle multiple versions side-by-side during rolling deploys.
- Use `request_id` for end-to-end tracing across HTTP → NATS boundaries.

### Retry and Resilience Behaviour

Hermes retries transient NATS publish failures with exponential backoff:

| Setting                    | Default | Description                                                     |
|----------------------------|---------|-----------------------------------------------------------------|
| `PUBLISH_RETRIES`          | 3       | Total publish attempts before giving up                         |
| `PUBLISH_RETRY_BASE_DELAY` | 0.1 s   | Base delay; actual = `base × 2^attempt` ± jitter, capped at 2 s |
| `NATS_RETRY_ATTEMPTS`      | 3       | Initial-connect retries at startup before failing the boot      |
| `NATS_RETRY_INTERVAL`      | 5.0 s   | Delay between initial-connect retries at startup (not used by the per-publish retry path; surfaced on `/health`) |

Retryable errors: `TimeoutError`, `NoRespondersError`, `DrainTimeoutError`,
`ConnectionReconnectingError`, `StaleConnectionError`. Non-retryable errors propagate immediately
and the message is dead-lettered (if `ENABLE_DEAD_LETTER=true`).

---

## §3 — Downstream Consumers

These services are verified in `CLAUDE.md` and the repository architecture:

| Service      | Role                  | Subscribes to                          | Notes                                          |
|--------------|-----------------------|----------------------------------------|------------------------------------------------|
| **Argus**    | Observability         | `hi.agents.>`, `hi.tasks.>`            | Monitoring; no action taken                    |
| **Agamemnon**| Coordination          | External; Hermes calls it via HTTP     | `AGAMEMNON_URL` + `AGAMEMNON_API_KEY` required |
| **Telemachy**| Workflow engine       | `hi.tasks.>`                           | Drives task workflows from task events         |

Unknown event types go to `homeric-deadletter` for inspection and replay.

### Not Yet Integrated

> **Task events** (`task.updated`, `task.completed`, `task.failed`) are defined as
> forward-compatible scaffolding but are **not yet emitted** by the upstream service.
> See [Odysseus#33](https://github.com/HomericIntelligence/Odysseus/issues/33).

---

## §4 — Observability Endpoints

These endpoints are available for agents and operators monitoring Hermes:

| Endpoint        | Method | Description                                              |
|-----------------|--------|----------------------------------------------------------|
| `/health`       | GET    | Liveness probe; returns NATS connection state            |
| `/ready`        | GET    | Readiness probe; `503` when NATS is disconnected         |
| `/metrics`      | GET    | Prometheus metrics (counters, gauges, histograms)        |
| `/subjects`     | GET    | List of NATS subjects published in this session          |
| `/events`       | GET    | Canonical list of supported event types                  |
| `/dead-letters` | GET    | View in-memory dead-letter queue (paginated)             |
| `/dead-letters` | DELETE | Drain (clear) the dead-letter queue                      |
| `/version`      | GET    | Hermes version string                                    |

---

## §5 — Coding Agent Guidance

Rules for AI agents making code changes in this repository:

1. **Never block the event loop.** FastAPI and nats-py are fully async. All I/O must use `await`.

2. **Always use `_slug()` for subject tokens.** Never hand-roll NATS subject strings; import
   `_slug` from `hermes.publisher` to ensure sanitisation is consistent.

3. **Config via environment variables only.** No hard-coded URLs or secrets. All tunables live in
   `hermes/config.py` (`Settings`), sourced from env vars / `.env`.

4. **Pin dependency versions** in `pixi.toml` using `>=X.Y,<NEXT_MAJOR`. Never use `*`.

5. **Run tests before committing:**

   ```bash
   just test          # pytest
   just lint          # ruff check
   just format        # ruff format
   ```

6. **Subject slug limit is 64 characters per token** (`_SLUG_MAX_LEN = 64` in `publisher.py`).
   Values are silently truncated — design data fields accordingly.

7. **Wire format changes require a `schema_version` bump** only for breaking changes. Additive
   fields (new optional keys) are backwards-compatible and do not need a version increment.

8. **`WEBHOOK_SECRET` minimum length is 32 characters.** Shorter values are rejected at startup.

---

## §6 — Quick Reference: Event Flow

```
Producer agent                        Hermes                          NATS
─────────────                         ──────                          ────
POST /webhook ──────────────────────► validate HMAC
  {                                   parse event type
    "event": "agent.created",         resolve subject
    "data": {                         ─────────────────────────────► hi.agents.host.name.created
      "hostId": "docker-desktop",                                     (homeric-agents stream)
      "name":   "researcher"
    },                                                          Consumer agents
    "timestamp": "2026-05-05T..."                               ──────────────
  }                                                             subscribe to hi.agents.> or
◄─────────────────────────────────── 202 Accepted              hi.agents.docker-desktop.#
  {"status":"accepted",               {"schema_version":1,     read schema_version
   "event":"agent.created"}            "event":"agent.created", deserialize data{}
                                       "data":{...},
                                       "timestamp":"...",
                                       "request_id":"..."}
```
