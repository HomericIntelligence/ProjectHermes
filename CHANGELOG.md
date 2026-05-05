# Changelog

All notable changes to ProjectHermes are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Prometheus metrics endpoint (`/metrics`) with request count, latency histograms, and NATS
  publish counters
- Graceful shutdown sequence with configurable `SHUTDOWN_TIMEOUT` (default 10 s)
- NATS reconnect retry loop with exponential backoff and jitter; oversized-payload logging
- Request ID middleware: `X-Request-ID` header propagated through all log lines
- Dead-letter queue routing for unknown event types (`homeric-deadletter` stream)
- `MAX_PAYLOAD_BYTES` configuration variable (default 1 MiB) with startup logging
- TLS verification guard: loud `WARNING` when `TLS_VERIFY=false` + `HERMES_HOST=0.0.0.0`
- `schema_version` field documented in wire format reference
- Docker publish workflow for automated image releases on version tags
- Pre-commit CI step and version-consistency check in CI pipeline
- `scan-secrets` just recipe using gitleaks
- pip-audit step in CI for dependency vulnerability scanning
- Unified `_required.yml` required-checks workflow

### Changed

- Default server port changed from 8080 to 8085 in Dockerfile
- CI `typecheck` steps merged into the `lint` job to reduce workflow complexity
- NATS connection logic extracted into `_connect_to_nats()` to reduce lifespan complexity
- Pre-commit mypy hook added; ruff removed from pre-commit (handled by CI lint step)

### Fixed

- Signal handler registration guarded against non-main-thread execution (`RuntimeError` caught)
- NATS retry loop: skip sleep after final attempt; log "giving up" on terminal failure
- lifespan abort and degraded-mode behavior aligned across server and tests
- mypy type errors exposed by new pre-commit mypy check
- `pixi.toml` lock file regenerated to match pinned pixi version (v0.67.2)
- `gitleaks-action` reverted (requires org license); pip-audit set to `continue-on-error`
- CI: valid job IDs in `_required.yml` (no slashes in keys)
- `prometheus_client` added to Docker image dependencies

## [0.1.0] - 2026-04-03

Initial release of ProjectHermes — a lightweight Python service that bridges external
webhooks to NATS JetStream for the HomericIntelligence ecosystem.

### Added

- Initial ProjectHermes scaffold: FastAPI service, NATS JetStream publisher, HMAC webhook
  validation, and subject routing (`hi.agents.*` / `hi.tasks.*`)
- Dockerfile for container builds; non-root user execution
- `just` command runner integration: `just dev`, `just test`, `just lint`, `just format`,
  `just health`, `just nats-start`, `just register-webhook`
- CI pipeline with ruff linting, mypy type checking, coverage reporting, and Docker build
- LICENSE (MIT), SECURITY.md, CONTRIBUTING.md, CODE_OF_CONDUCT.md

### Changed

- Decoupled from ai-maestro: Hermes now bridges arbitrary external webhooks → NATS
  (ADR-006), removing the ai-maestro-specific coupling
- Migrated configuration to `pydantic-settings` for structured env-var management
- Updated package description to reflect the post-migration role

### Fixed

- Correct webhook registration payload, HMAC validation, and subject extraction from
  payload shape
- `pixi.toml` schema (`workspace.dependencies` → `dependencies`); added `pyproject.toml`;
  all tests now pass
- JetStream streams created on connect; event types corrected for webhook API
- Missing webhook event subscriptions migrated to pydantic-settings
- CI: ruff unused-import errors, `just` dependency added to pixi, pixi.lock updated
- Docker: service now runs as non-root user

[Unreleased]: https://github.com/HomericIntelligence/ProjectHermes/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/HomericIntelligence/ProjectHermes/releases/tag/v0.1.0
