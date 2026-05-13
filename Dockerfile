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
RUN python3 -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); print('\n'.join(d['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install --no-cache-dir --target /build/deps -r /tmp/requirements.txt

FROM python:3.12-slim@sha256:ec948fa5f90f4f8907e89f4800cfd2d2e91e391a4bce4a6afa77ba265bc3a2fe
WORKDIR /app

# Pin tini to a specific Debian package version for reproducibility (see CLAUDE.md §6).
# Bump together with the python:3.12-slim digest above; verify the version is available with:
#   docker run --rm python:3.12-slim apt-cache policy tini
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini=0.19.0-1 \
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
