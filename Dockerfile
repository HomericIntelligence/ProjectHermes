FROM python:3.12-slim AS builder
WORKDIR /build
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" nats-py "pydantic>=2.0" pydantic-settings httpx \
    --target /build/deps

FROM python:3.12-slim
WORKDIR /app

COPY --from=builder /build/deps /usr/local/lib/python3.12/site-packages/
COPY src/hermes/ ./hermes/

RUN useradd -r -s /usr/sbin/nologin hermes
USER hermes

ENV HERMES_PORT=8080
ENV HERMES_PUBLIC_URL=http://localhost:8080
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${HERMES_PORT:-8080}/ready || exit 1

CMD ["python", "-m", "hermes.server"]
