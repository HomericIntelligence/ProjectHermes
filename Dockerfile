FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" nats-py "pydantic>=2.0" pydantic-settings httpx
COPY src/hermes/ ./hermes/
EXPOSE 8085
ENV HERMES_PORT=8085
CMD ["python", "-m", "hermes.server"]
