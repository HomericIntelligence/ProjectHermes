"""Registrar module removed per ADR-006.

Previously registered ProjectHermes as a webhook receiver with ai-maestro.
After migration to ProjectAgamemnon, Hermes no longer registers with an
orchestrator. External services (GitHub, Slack, etc.) configure their
webhooks to point directly at Hermes's /webhook endpoint.

The canonical supported event lists are:
  hermes.publisher.AGENT_EVENTS  — agent.created, agent.updated, agent.deleted
  hermes.publisher.TASK_EVENTS   — task.updated, task.completed, task.failed

Query the live list at runtime: GET /events
"""
