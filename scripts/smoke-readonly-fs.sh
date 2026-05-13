#!/usr/bin/env bash
# scripts/smoke-readonly-fs.sh
#
# Integration smoke test for read-only root filesystem enforcement.
#
# Spins up the Hermes container via docker compose (which sets
# `read_only: true` and a writable `tmpfs: /tmp`), waits for the /ready
# health endpoint, then asserts:
#
#   1. Container starts cleanly and reaches a healthy /ready state.
#   2. Writing to the root filesystem (`/test-write`) fails (non-zero exit).
#   3. Writing to the tmpfs mount (`/tmp/test-write`) succeeds (exit zero).
#   4. A simple webhook request returns HTTP 401 (unsigned) — not 500 —
#      which proves the read-only FS does not crash the request path
#      (PYTHONDONTWRITEBYTECODE prevents .pyc writes on import).
#
# Run locally:
#   bash scripts/smoke-readonly-fs.sh
#
# Run in CI: see `.github/workflows/_required.yml` → `readonly-fs-smoke`.
set -euo pipefail

ROOT="$(cd "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"

# WEBHOOK_SECRET must satisfy the 32-char minimum enforced by hermes.config.
export WEBHOOK_SECRET="${WEBHOOK_SECRET:-smoke-test-secret-for-readonly-fs-min32}"

cleanup() {
  docker compose logs hermes >&2 || echo "(no hermes logs available)" >&2
  docker compose down --volumes --remove-orphans >/dev/null 2>&1 || \
    echo "WARN: docker compose down failed during cleanup" >&2
}
trap cleanup EXIT

echo "==> Building and starting docker compose stack"
docker compose up --build -d

echo "==> Waiting for hermes /ready (up to 60s)"
ready=0
for i in $(seq 1 60); do
  if docker compose exec -T hermes curl -sf "http://localhost:8085/ready" >/dev/null 2>&1; then
    ready=1
    echo "    /ready ok after ${i}s"
    break
  fi
  sleep 1
done

if [ "$ready" -ne 1 ]; then
  echo "ERROR: hermes /ready did not become healthy within 60s" >&2
  exit 1
fi

echo "==> Asserting root filesystem is read-only"
if docker compose exec -T hermes touch /test-write 2>/dev/null; then
  echo "ERROR: touch /test-write succeeded — root FS is NOT read-only" >&2
  exit 1
fi
echo "    OK: touch /test-write rejected as expected"

echo "==> Asserting /tmp is writable (tmpfs mount)"
if ! docker compose exec -T hermes touch /tmp/test-write; then
  echo "ERROR: touch /tmp/test-write failed — /tmp is not writable" >&2
  exit 1
fi
docker compose exec -T hermes rm -f /tmp/test-write
echo "    OK: touch /tmp/test-write succeeded"

echo "==> Posting an unsigned webhook (expect HTTP 401, not 500)"
status="$(docker compose exec -T hermes \
  curl -s -o /dev/null -w '%{http_code}' \
  -X POST http://localhost:8085/webhook \
  -H 'Content-Type: application/json' \
  -d '{"event":"smoke.test","data":{},"timestamp":"2026-05-12T00:00:00Z"}' \
)"
case "$status" in
  401|400|403|422)
    echo "    OK: webhook returned HTTP ${status} (rejected, not crashed)"
    ;;
  5*)
    echo "ERROR: webhook returned HTTP ${status} — container appears to crash on FS writes" >&2
    exit 1
    ;;
  *)
    echo "ERROR: unexpected webhook HTTP status: ${status}" >&2
    exit 1
    ;;
esac

echo "==> Confirming container is still running (no crash)"
state="$(docker compose ps --status running --services | tr '\n' ' ')"
case " ${state} " in
  *" hermes "*) echo "    OK: hermes still running" ;;
  *)
    echo "ERROR: hermes is no longer running after smoke test: '${state}'" >&2
    exit 1
    ;;
esac

echo "==> read-only filesystem smoke test PASSED"
