# ADR-002: Dead-Letter Strategy for Unknown Event Types

**Status:** Accepted
**Date:** 2024-01-15
**Deciders:** Hermes maintainers

---

## Context

External services POST webhook payloads to Hermes. Each payload carries an `event` field (e.g.,
`agent.created`, `task.completed`). Hermes routes known event types to well-defined NATS subjects
(`hi.agents.*` and `hi.tasks.*`). However, external services may send event types that:

- Are not yet implemented in Hermes (forward-compatible events from a newer upstream version).
- Are misconfigured (typos, renamed event strings in upstream service).
- Represent intentional extensions added by an operator without a corresponding Hermes update.

Silently dropping unknown events means operators have no way to inspect, diagnose, or replay them.
Raising a hard error and returning `4xx` to the caller is also wrong: the event is not malformed —
it is simply unrecognised, and the caller cannot be expected to know Hermes's routing table.

---

## Decision

Unknown event types are handled through a two-tier dead-letter mechanism, gated behind the
`ENABLE_DEAD_LETTER` environment variable (default: `true`):

**Tier 1 — JetStream dead-letter stream:**
Unknown events are published to `hi.deadletter.<event-slug>` in NATS JetStream. JetStream provides
durable storage, so events can be replayed after Hermes or consumer code is updated. The subject
uses the sanitised event slug so consumers can subscribe selectively.

**Tier 2 — In-memory deque:**
A bounded deque (max 1000 entries) maintains a local copy of dead-lettered events for operator
inspection via the `GET /dead-letters` endpoint. This allows immediate visibility without querying
NATS.

If `ENABLE_DEAD_LETTER=false`, unroutable events raise `UnknownEventTypeError` and the caller
receives `422 Unprocessable Entity`.

---

## Rationale

- **Never silently drop events:** A dropped webhook that should have triggered a downstream action
  (e.g., agent state change) can cause cascading failures that are hard to diagnose.
- **JetStream durability:** The dead-letter stream persists across Hermes restarts. An operator can
  replay events after updating routing logic, without needing the upstream service to re-fire.
- **In-memory view for rapid inspection:** The `/dead-letters` endpoint gives operators immediate
  visibility without requiring NATS CLI access. The 1000-entry cap keeps memory usage bounded.
- **Operator opt-out:** `ENABLE_DEAD_LETTER=false` supports strict environments where receiving
  any unknown event type should be treated as a configuration error.
- **Selective subscription:** Using `hi.deadletter.<event-slug>` subjects (rather than one flat
  queue) allows consumers to subscribe to specific unrecognised event types independently.

---

## Consequences

- The `hi.deadletter.>` JetStream stream must be provisioned in NATS before Hermes starts (or
  Hermes must be granted stream creation permissions). The stream is created automatically on
  startup via `_ensure_streams()` in `publisher.py` if JetStream admin permissions are available.
- The in-memory deque is lost on restart; only the JetStream copy is durable.
- Operators monitoring the dead-letter queue should set up alerts on the `hi.deadletter.>` stream
  subject pattern and on the `dead_letter_count` field in the `/health` response.
- Callers always receive `200 OK` for unknown events when dead-lettering is enabled, which is the
  correct HTTP contract (the payload was received and processed — routing it to dead-letter *is*
  the processing).

---

## Alternatives Considered

| Alternative                         | Reason Rejected                                                                                             |
|-------------------------------------|-------------------------------------------------------------------------------------------------------------|
| Return `422` for all unknown events | Breaks callers that send forward-compatible events; shifts routing knowledge burden onto upstream services. |
| Single flat `hi.deadletter` subject | Prevents selective subscription; makes replay and monitoring coarser.                                       |
| Database-backed dead-letter queue   | Adds a stateful dependency; JetStream already provides durable storage.                                     |
| In-memory only (no JetStream tier)  | Events are lost on restart; no replay capability.                                                           |

---

## Document Metadata

**Status:** Accepted
**Supersedes:** N/A
**Superseded by:** N/A
