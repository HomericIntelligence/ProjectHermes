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
# Supported event types (canonical source: src/hermes/publisher.py):
#   Agent events: agent.created, agent.updated, agent.deleted
#   Task events:  task.updated, task.completed, task.failed
#
# Query the live event list: GET http://<hermes-host>:<HERMES_PORT>/events
#
# See CLAUDE.md for configuration details.

echo "NOTE: Webhook registration with ai-maestro has been removed (ADR-006)."
echo "Configure external services to call POST http://localhost:${HERMES_PORT:-8080}/webhook directly."
echo "Supported events: agent.created, agent.updated, agent.deleted, task.updated, task.completed, task.failed"
echo "Live event list:  GET http://localhost:${HERMES_PORT:-8080}/events"
