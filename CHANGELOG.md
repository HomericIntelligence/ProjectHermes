# Changelog

All notable changes to ProjectHermes are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Prometheus metrics endpoint (`/metrics`) with `prometheus_client` dependency (#415)
- Docker publish workflow for automated container image releases
- `scan-secrets` recipe and `pip-audit` security scanning added to CI summary
- Pre-commit integration in CI with version consistency check

### Changed

- Default server port changed from 8080 to 8085 (#415)
- Merged typecheck steps into single lint CI job (#315)
- Added unified required-checks workflow (`_required.yml`) to gate merges
- Refactored NATS connection into `_connect_to_nats` to reduce lifespan complexity
- Documented `schema_version` wire format and `TLS_VERIFY` guard in CLAUDE.md

### Fixed

- NATS retry loop: skip sleep after final attempt; log "giving up" on exhausted retries
- Server: oversized payloads now logged before rejection; `MAX_PAYLOAD_BYTES` documented
- Server: graceful lifespan failure on NATS unavailability; `HERMES_HOST` default in Dockerfile
- Server: signal handler guarded against non-main-thread `RuntimeError`
- Docker: `prometheus_client` added to image; default port corrected to 8085 (#415)
- CI: invalid job IDs (slashes) in `_required.yml` corrected
- CI: `ruff` removed from pre-commit config; lint step handles ruff checks directly
- CI: `pixi` pinned to v0.67.2 to match regenerated lock file; `pip-audit` marked `continue-on-error`
- CI: reverted `gitleaks-action` (requires org license)
- CI: mypy type errors exposed by new pre-commit mypy check resolved

### Security

- `pip-audit` dependency vulnerability scanning added to CI pipeline
- Secrets scanning recipe (`scan-secrets`) added via `just`

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
