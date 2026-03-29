"""
registrar.py — REMOVED (ADR-006)

Previously registered ProjectHermes as a webhook receiver with ai-maestro.
After migration to ProjectAgamemnon, Hermes no longer registers with an
orchestrator. External services (GitHub, Slack, etc.) configure their
webhooks to point directly at Hermes's /webhook endpoint.
"""
