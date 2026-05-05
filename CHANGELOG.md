# Changelog

All notable changes to ProjectHermes are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Docker publish workflow to automate image releases (156cef0)

### Changed

- Merge typecheck steps into lint job for unified CI pipeline (#315)
- Add unified required-checks workflow (`_required.yml`) (3583359)
- Add pre-commit to CI; add version consistency check (3bfee23)
- Add pip-audit to CI summary; pin gitleaks action; add `scan-secrets` recipe (d74b821)
- Add `schema_version` documentation, TLS\_VERIFY startup guard, and pip-audit CI step (cda3225)
- Add integration tests: reconnect lifecycle, bind host, retry, `request_id` in NATS publish (2d65171)
- Add integration tests: lifespan abort, shutdown sequence, startup banner (fe8f8e0)
- Refactor `_connect_to_nats` extraction to reduce lifespan complexity (16dbf0e)
- Add session artifact paths to `.gitignore` (64b45d5)

### Fixed

- Docker: add `prometheus_client` dependency; correct default port to 8085 (#415)
- CI: use valid job IDs in `_required.yml` (no slashes in keys) (76c2eaf)
- CI: remove ruff from pre-commit; CI lint step handles ruff checks (42c2541)
- CI: use pixi pre-commit, pin ruff to 0.15.12 to match pixi environment (e538b61)
- Deps: regenerate `pixi.lock` to reflect updated source hash (e5b52de)
- CI: revert pixi pin to latest; remove duplicate pip-audit step (ec9951a)
- CI: pin pixi to v0.67.2 to match regenerated lock file (6a4bd3f)
- CI: resolve mypy type errors exposed by new pre-commit mypy hook (dccdf2d)
- Tests: set `reconnect_count` and `last_error` on mock publisher for 503 test (1a90823)
- Server: catch `RuntimeError` from `add_signal_handler` in non-main threads (959be06)
- Server: extract NATS retry loop, fix C901 complexity, apply ruff format (e74568a)
- Server: guard signal handler registration against non-main-thread execution (e358985)
- Tests: align lifespan tests with degraded-mode behavior (3da135f)
- Server: graceful lifespan failure on NATS unavailability; Dockerfile `HERMES_HOST` default (9bfcbd0)
- Tests: patch `setup_logging` in startup banner test to preserve caplog (74cb7bf)
- Deps: regenerate `pixi.lock` with pixi 0.67.2 (7905e70)
- CI: mark pip-audit as `continue-on-error` (35f88e4)
- Server: log 'giving up' on final NATS retry; fix test import of removed constant (499b055)
- Server: skip sleep after last NATS retry; log oversized payloads; document `MAX_PAYLOAD_BYTES` (86ac954)
- CI: revert gitleaks-action (requires org license); pip-audit `continue-on-error` (13adbb3)

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
