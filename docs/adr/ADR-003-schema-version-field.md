# ADR-003: Wire Format Schema Versioning

**Status:** Accepted
**Date:** 2024-01-15
**Deciders:** Hermes maintainers

---

## Context

Hermes publishes structured JSON messages to NATS JetStream. Multiple downstream consumers
(Argus, Agamemnon, Telemachy) deserialize these messages independently. Over time, the wire format
will evolve: fields may be added, renamed, or removed.

Without a versioning mechanism, a breaking wire format change requires all consumers to be updated
and deployed simultaneously — a hard coordination problem in a distributed system with rolling
deployments.

Content-negotiation patterns (HTTP `Accept` / `Content-Type` headers) do not apply to pub/sub
systems: there is no per-consumer negotiation channel between the publisher and individual
subscribers in NATS JetStream.

---

## Decision

Every message published by Hermes includes a `schema_version: int` field at the top level of the
JSON payload. This field is defined in `HermesEventBase` in `src/hermes/models.py`:

```python
schema_version: int = Field(default=1, ge=1, description="Wire format schema version")
```

**Versioning rules:**

| Change type                  | Version bump?             |
|------------------------------|---------------------------|
| New **optional** field added | No — backwards-compatible |
| Existing field **renamed**   | Yes — breaking change     |
| Existing field **removed**   | Yes — breaking change     |
| Field **type changed**       | Yes — breaking change     |
| New **required** field added | Yes — breaking change     |

The current version is **1**. All messages carry `"schema_version": 1` until a breaking change
warrants incrementing to `2`.

**Consumer contract:**

1. Deserialize only `schema_version` first (it is always at the top level).
2. If `schema_version` is higher than the consumer's maximum known version, log a warning and
   route the message to a dead-letter queue rather than crashing.
3. If `schema_version` matches a known version, deserialize the full payload accordingly.
4. During rolling deployments, consumers must handle messages from both the old and new schema
   version simultaneously.

---

## Rationale

- **Decoupled upgrades:** Publishers (Hermes) and consumers can be deployed independently.
  Consumers detect version mismatches at message time rather than at deploy time.
- **Simplicity over negotiation:** A single integer in the payload is universally parseable by any
  JSON consumer without schema registry infrastructure.
- **Conservative increment policy:** Bumping only on breaking changes means consumers do not need
  to update code for additive changes, reducing deployment churn.
- **Fail-safe consumer guidance:** Routing unknown versions to dead-letter (rather than crashing)
  preserves events for later replay once the consumer is updated.
- **Matches JetStream semantics:** JetStream already provides replay; pairing it with
  `schema_version` gating gives consumers a complete upgrade path: old messages are replayable
  with an updated consumer after a version bump.

---

## Consequences

- All NATS consumers **must** read `schema_version` before full deserialization. This is a
  non-negotiable contract for any service subscribing to Hermes-published subjects.
- There is no automated migration: old messages stored in JetStream retain their original
  `schema_version`. A consumer updating from v1 to v2 must handle both.
- The `schema_version` field is validated with `ge=1` in Pydantic; values less than 1 are
  rejected at the Hermes boundary and will never be published.
- Additive changes (new optional fields) require no consumer-side code changes and no version
  bump, which reduces the cost of evolving the wire format.

---

## Alternatives Considered

| Alternative                                       | Reason Rejected                                                                                          |
|---------------------------------------------------|----------------------------------------------------------------------------------------------------------|
| NATS subject-per-version (e.g., `hi.agents.v2.*`) | Subject proliferation; consumers must subscribe to N subjects; complicates routing logic.                |
| Schema registry (e.g., Confluent Schema Registry) | External infrastructure dependency; significant operational overhead for a lightweight bridge.           |
| Semantic versioning string (e.g., `"1.0.0"`)      | Semver comparison logic in every consumer; integer comparison is simpler and sufficient.                 |
| No versioning                                     | First breaking change requires coordinated fleet-wide deployment; unacceptable for a distributed system. |

---

## Document Metadata

**Status:** Accepted
**Supersedes:** N/A
**Superseded by:** N/A
