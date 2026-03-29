#!/usr/bin/env bash
# register-webhooks.sh
#
# DEPRECATED (ADR-006): Hermes no longer registers with an orchestrator.
#
# External services (GitHub, Slack, etc.) should configure their webhooks
# to point directly at this Hermes instance:
#
#   POST http://<hermes-host>:<HERMES_PORT>/webhook
#
# Hermes validates the HMAC signature (WEBHOOK_SECRET) and publishes
# the event to NATS JetStream.
#
# See CLAUDE.md for configuration details.

echo "NOTE: Webhook registration with ai-maestro has been removed (ADR-006)."
echo "Configure external services to call POST http://localhost:${HERMES_PORT:-8080}/webhook directly."
