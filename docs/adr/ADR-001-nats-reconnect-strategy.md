# ADR-001: NATS Reconnect Strategy

**Status:** Accepted
**Date:** 2024-01-15
**Deciders:** Hermes maintainers

---

## Context

The nats-py client library supports automatic background reconnection: when the TCP connection to
NATS drops, the library silently buffers outgoing messages and re-delivers them once the connection
is restored. This is convenient but introduces hidden failure modes for a webhook bridge:

- Buffered messages may be delivered out-of-order or after a significant delay, making events
  appear stale to downstream consumers.
- The HTTP caller receives a `200 OK` response even though the message has not been durably
  published to JetStream — a false acknowledgement.
- The `/health` and `/ready` endpoints cannot accurately report the service as unhealthy while the
  library is silently attempting to reconnect in the background.

Hermes is a thin, stateless bridge: its only job is to receive an HTTP webhook and durably publish
it to NATS. If that publish cannot succeed immediately, the caller should know.

---

## Decision

Hermes sets `allow_reconnect=False` when calling `nats.connect()` in `publisher.py`.

Reconnection to NATS is handled **only at startup** via `_connect_with_retries()` in `server.py`,
which retries with exponential backoff (default: 3 attempts, 5-second intervals) before allowing
the service to start accepting traffic.

Once the service is running, a NATS disconnect causes:

1. `_on_disconnected()` callback fires; `publisher._connected` is set to `False`.
2. `/health` and `/ready` return `503 Service Unavailable`.
3. Incoming `POST /webhook` requests receive `503` immediately (checked before publishing).
4. The operator is alerted and must restart Hermes or restore NATS connectivity.

---

## Rationale

- **Observability over convenience:** A `503` response to the upstream webhook caller is honest.
  The caller's retry logic handles re-delivery, which is better than Hermes silently buffering and
  guessing when to flush.
- **JetStream acknowledgements are the source of truth:** Only after `js.publish()` returns
  successfully has the message been durably stored. Allowing reconnect would mean returning `200
  OK` before that guarantee is met.
- **Startup retries cover the common case:** The most frequent reason Hermes cannot reach NATS is
  that both services are starting simultaneously (e.g., `docker compose up`). Startup retries
  handle this without compromising the runtime contract.
- **Simplicity:** One clear failure mode (disconnect → 503 → restart) is easier to operate and
  alert on than a partially-connected buffering state.

---

## Consequences

- Hermes must be restarted (or NATS must recover and Hermes restarted) after a persistent NATS
  disconnect; there is no self-healing at runtime.
- Upstream callers must implement retry logic with backoff. This is standard practice for webhook
  delivery (GitHub, Slack, and most SaaS platforms retry on `5xx` responses).
- The `/health` endpoint accurately reflects the NATS connection state, enabling reliable liveness
  and readiness probes.

---

## Alternatives Considered

| Alternative | Reason Rejected |
|-------------|----------------|
| `allow_reconnect=True` (library default) | Silent buffering produces false `200 OK` responses and makes health probes unreliable. |
| `allow_reconnect=True` + in-memory buffer with timeout | Adds complexity; still risks stale delivery; does not eliminate the false-ACK problem for JetStream. |
| Circuit-breaker middleware | Adds a dependency and operational complexity for a problem that startup retries already solve. |

---

## Document Metadata

**Status:** Accepted
**Supersedes:** N/A
**Superseded by:** N/A
