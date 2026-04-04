FROM python:3.11-slim

WORKDIR /app

# Install dependencies first for layer caching
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Copy source (invalidates cache only on code changes)
COPY src/ src/

# Re-install with source to pick up the actual package code
RUN pip install --no-cache-dir .

EXPOSE 8085

ENV NATS_URL=nats://localhost:4222
ENV HERMES_PORT=8085

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:${HERMES_PORT}/health')" || exit 1

RUN useradd -r -s /usr/sbin/nologin hermes
USER hermes

CMD ["python", "-m", "hermes.server"]
