# Architecture Decision Records

This directory contains Architecture Decision Records (ADRs) for Hermes-specific design decisions.

ADRs document the context, decision, and consequences of significant architectural choices so that
future maintainers understand *why* the code is the way it is.

## Format

Each ADR uses the following sections: **Status**, **Date**, **Deciders**, **Context**,
**Decision**, **Rationale**, **Consequences**, **Alternatives Considered**.

## Status Lifecycle

`Proposed` → `Accepted` → `Deprecated` / `Superseded by ADR-NNN`

## Index

| #                                             | Title                                        | Status   | Date       |
|-----------------------------------------------|----------------------------------------------|----------|------------|
| [ADR-001](ADR-001-nats-reconnect-strategy.md) | NATS Reconnect Strategy                      | Accepted | 2024-01-15 |
| [ADR-002](ADR-002-dead-letter-strategy.md)    | Dead-Letter Strategy for Unknown Event Types | Accepted | 2024-01-15 |
| [ADR-003](ADR-003-schema-version-field.md)    | Wire Format Schema Versioning                | Accepted | 2024-01-15 |
