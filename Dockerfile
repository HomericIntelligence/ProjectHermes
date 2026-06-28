# Pin python:3.12-slim by digest for reproducible builds. Bump in BOTH FROM lines together.
# Refresh procedure (also in CONTRIBUTING.md → Bumping the NATS Image Digest):
#   docker pull python:3.12-slim
#   docker inspect --format '{{index .RepoDigests 0}}' python:3.12-slim
FROM python:3.12-slim@sha256:ec948fa5f90f4f8907e89f4800cfd2d2e91e391a4bce4a6afa77ba265bc3a2fe AS builder
WORKDIR /build
COPY pyproject.toml .
# Extract runtime deps to a newline-delimited requirements list, then feed pip via stdin.
# Avoids shell-quoting hazards from version specifiers like `<1` (shell redirects) or
# bare `repr()` output (pip rejects `'fastapi>=0.115,<1'` as quoted package name).
#
# Note on --no-deps (see #512): this is the ONLY pip install in the build — the runtime
# image receives /build/deps via COPY and runs `python -m hermes.server` against source
# copied from src/hermes/, with no second `pip install .` step. Adding --no-deps here
# would skip transitive dependencies (anyio, pydantic-core, starlette, etc.) and break
# the image. The two-layer "deps cached + source install --no-deps" pattern requires a
# separate package-install step that Hermes does not have; if this build is ever
# refactored to install a wheel, revisit #512 at that point.
RUN python3 -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); print('\n'.join(d['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install --no-cache-dir --target /build/deps -r /tmp/requirements.txt

FROM python:3.12-slim@sha256:ec948fa5f90f4f8907e89f4800cfd2d2e91e391a4bce4a6afa77ba265bc3a2fe
WORKDIR /app

# Install tini for PID-1 signal forwarding. The python:3.12-slim base image is already
# pinned by SHA256 digest above, which makes the tini package version reproducible
# without an apt version pin (apt pins drift as Debian rebuilds packages even when
# upstream version is unchanged, e.g. 0.19.0-1 → 0.19.0-1build1).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

# Install curl for the HEALTHCHECK directive below. Intentionally NOT version-pinned
# to avoid bumping in lockstep with the base image; the HEALTHCHECK only uses curl's
# stable -f flag. See issue #561.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /build/deps /usr/local/lib/python3.12/site-packages/
COPY src/hermes/ ./hermes/

RUN useradd -r -s /usr/sbin/nologin hermes
USER hermes

ENV HERMES_HOST=0.0.0.0
ENV HERMES_PORT=8085
ENV HERMES_PUBLIC_URL=http://localhost:8085
ENV PYTHONDONTWRITEBYTECODE=1
EXPOSE 8085

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${HERMES_PORT:-8085}/ready || exit 1

ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "hermes.server"]
