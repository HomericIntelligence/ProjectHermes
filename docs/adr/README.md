# Architecture Decision Records

This directory contains Architecture Decision Records (ADRs) for Hermes-specific design decisions.

ADRs document the context, decision, and consequences of significant architectural choices so that
future maintainers understand *why* the code is the way it is.

## Format

Each ADR uses the following sections: **Status**, **Date**, **Deciders**, **Context**,
**Decision**, **Rationale**, **Consequences**, **Alternatives Considered**.

## Status Lifecycle

`Proposed` → `Accepted` → `Deprecated` / `Superseded by ADR-NNN`

## Updating an ADR Status

ADRs are append-mostly: once Accepted, the body is **not** rewritten. To change a status:

1. Edit two places in the ADR file: the **header** (`**Status:** ...` near the top) and the
   **Document Metadata** footer (`**Status:** ...` at the bottom). They must match — drift
   between header and footer is a review red flag.
2. If superseding, also fill in `**Superseded by:** ADR-NNN` in the footer of the old ADR and add
   `**Supersedes:** ADR-MMM` in the footer of the new one.
3. Update the **Status** column of the index table below so the index never drifts from the file
   body.
4. Open a PR titled `docs(adr): mark ADR-NNN as <new-status>` with the rationale in the body.
   Approval requires at least one Hermes maintainer; the PR must not touch the ADR's Context,
   Decision, Rationale, Consequences, or Alternatives Considered sections — those remain
   historical record.

## Index

| #                                             | Title                                        | Status   | Date       |
|-----------------------------------------------|----------------------------------------------|----------|------------|
| [ADR-001](ADR-001-nats-reconnect-strategy.md) | NATS Reconnect Strategy                      | Accepted | 2024-01-15 |
| [ADR-002](ADR-002-dead-letter-strategy.md)    | Dead-Letter Strategy for Unknown Event Types | Accepted | 2024-01-15 |
| [ADR-003](ADR-003-schema-version-field.md)    | Wire Format Schema Versioning                | Accepted | 2024-01-15 |
