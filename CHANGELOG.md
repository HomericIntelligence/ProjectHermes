# Changelog

All notable changes to ProjectHermes are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

- Add `CHANGELOG.md` following Keep a Changelog format
- Address documentation audit findings

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
