# Contributing to ProjectHermes

Thank you for your interest in contributing to ProjectHermes! This is the NATS event bridge
for the [HomericIntelligence](https://github.com/HomericIntelligence) distributed agent mesh —
it bridges external webhooks to NATS JetStream for pub/sub fan-out and event replay.

For an overview of the full ecosystem, see the
[Odysseus](https://github.com/HomericIntelligence/Odysseus) meta-repo.

## Quick Links

- [Development Setup](#development-setup)
- [What You Can Contribute](#what-you-can-contribute)
- [Development Workflow](#development-workflow)
- [Building and Testing](#building-and-testing)
- [Pull Request Process](#pull-request-process)
- [Code Review](#code-review)

## Development Setup

### Prerequisites

- [Git](https://git-scm.com/)
- [GitHub CLI](https://cli.github.com/) (`gh`)
- [Pixi](https://pixi.sh/) for environment management (installs Python 3.10+)
- [Just](https://just.systems/) as the command runner

### Environment Setup

```bash
# Clone the repository
git clone https://github.com/HomericIntelligence/ProjectHermes.git
cd ProjectHermes

# Activate the Pixi environment
pixi shell

# Copy and customize environment variables
cp .env.example .env

# Start in development mode (auto-reload)
just dev

# List available recipes
just --list
```

### Verify Your Setup

```bash
# Check service health
just health

# Run tests
just test
```

## What You Can Contribute

- **Webhook handlers** — New routes for receiving external events
- **NATS subject routing** — Event-to-subject mapping and fan-out logic
- **Event transformations** — Payload normalization and enrichment
- **Tests** — pytest + pytest-asyncio test cases
- **Dockerfile improvements** — Build optimization, security hardening
- **Documentation** — README updates, webhook integration guides

## Development Workflow

### 1. Find or Create an Issue

Before starting work:

- Browse [existing issues](https://github.com/HomericIntelligence/ProjectHermes/issues)
- Comment on an issue to claim it before starting work
- Create a new issue if one doesn't exist for your contribution

### 2. Branch Naming Convention

Create a feature branch from `main`:

```bash
git checkout main
git pull origin main
git checkout -b <issue-number>-<short-description>

# Examples:
git checkout -b 12-add-github-webhook-handler
git checkout -b 8-fix-nats-reconnect-logic
```

**Branch naming rules:**

- Start with the issue number
- Use lowercase letters and hyphens
- Keep descriptions short but descriptive

### 3. Commit Message Format

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```text
<type>(<scope>): <subject>

<body>

<footer>
```

**Types:**

| Type       | Description                |
|------------|----------------------------|
| `feat`     | New feature                |
| `fix`      | Bug fix                    |
| `docs`     | Documentation only         |
| `style`    | Formatting, no code change |
| `refactor` | Code restructuring         |
| `test`     | Adding/updating tests      |
| `chore`    | Maintenance tasks          |

**Example:**

```bash
git commit -m "feat(webhooks): add GitHub webhook handler

Receives GitHub push/PR events, validates signatures, transforms
payloads, and publishes to hi.pipeline.github.> subjects.

Closes #12"
```

## Building and Testing

### Test

```bash
# Run all tests (pytest + pytest-asyncio)
just test
```

### Lint and Format

```bash
# Run linter (ruff)
just lint

# Auto-format
just format
```

### Run Locally

```bash
# Start in development mode (uvicorn with auto-reload)
just dev

# Start in production mode
just start

# Start a local NATS server for development
just nats-start

# Check health endpoint
just health

```

### Python Conventions

- **Python version**: 3.10+ (managed by pixi)
- **Framework**: FastAPI with uvicorn
- **Validation**: Pydantic v2 models for all request/response schemas
- **Async**: Use `async`/`await` for all I/O operations
- **Type hints**: Required for all function parameters and return types
- **Build backend**: hatchling (`pyproject.toml`)

## Pull Request Process

### Before You Start

1. Ensure an issue exists for your work
2. Create a branch from `main` using the naming convention
3. Implement your changes
4. Run `just test` and `just lint` to verify

### Creating Your Pull Request

```bash
git push -u origin <branch-name>
gh pr create --title "[Type] Brief description" --body "Closes #<issue-number>"
```

**PR Requirements:**

- PR must be linked to a GitHub issue
- PR title should be clear and descriptive
- Tests and linting must pass

### Branching Strategy

| Branch type   | Naming convention  | Base branch | Notes                              |
|---------------|--------------------|-------------|------------------------------------|
| Default       | `main`             | —           | Protected; never push directly     |
| Feature / fix | `<issue>-<slug>`   | `main`      | e.g. `44-planning-templates`       |
| Release       | `release/v<x.y.z>` | `main`      | Created from `main` before tagging |

**Merge strategy:**

- Single-concern PRs: squash-and-merge
- Multi-commit story-arc PRs: rebase-and-merge

### Never Push Directly to Main

The `main` branch is protected. All changes must go through pull requests.

## Code Review

### What Reviewers Look For

- **Async correctness** — Are all I/O operations properly awaited?
- **Input validation** — Are webhook payloads validated with Pydantic models?
- **Error handling** — Are external service failures handled gracefully?
- **Test coverage** — Are new handlers covered by pytest-asyncio tests?
- **No hardcoded secrets** — Are credentials in environment variables?

### Responding to Review Comments

- Keep responses short (1 line preferred)
- Start with "Fixed -" to indicate resolution

## Markdown Standards

All documentation files must follow these standards:

- Code blocks must have a language tag (`python`, `bash`, `yaml`, `text`, etc.)
- Code blocks must be surrounded by blank lines
- Lists must be surrounded by blank lines
- Headings must be surrounded by blank lines

## Reporting Issues

### Bug Reports

Include: clear title, steps to reproduce, expected vs actual behavior, relevant logs.

### Security Issues

**Do not open public issues for security vulnerabilities.**
See [SECURITY.md](SECURITY.md) for the responsible disclosure process.

## Code of Conduct

Please review our [Code of Conduct](CODE_OF_CONDUCT.md) before contributing.

---

Thank you for contributing to ProjectHermes!
