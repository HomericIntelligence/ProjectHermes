# SPDX-License-Identifier: MIT
"""Prometheus metric singletons for ProjectHermes."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

WEBHOOKS_RECEIVED: Counter = Counter(
    "hermes_webhooks_received_total",
    "Total number of webhook requests received",
    ["event_type"],
)

WEBHOOKS_PUBLISHED: Counter = Counter(
    "hermes_webhooks_published_total",
    "Total number of webhooks successfully published to NATS",
    ["subject_prefix"],
)

WEBHOOKS_FAILED: Counter = Counter(
    "hermes_webhooks_failed_total",
    "Total number of webhook processing failures",
    ["reason"],
)

DEAD_LETTERS: Counter = Counter(
    "hermes_dead_letters_total",
    "Total number of events with no subject mapping (dropped)",
    ["event_type"],
)

PUBLISH_LATENCY: Histogram = Histogram(
    "hermes_publish_duration_seconds",
    "Time spent publishing a webhook payload to NATS JetStream",
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
)

ACTIVE_SUBJECTS: Gauge = Gauge(
    "hermes_active_subjects_count",
    "Number of distinct NATS subjects that have been published to",
)

NATS_RECONNECTS: Counter = Counter(
    "hermes_nats_reconnects_total",
    "Total number of external NATS reconnect attempts",
    ["result"],
)

DEAD_LETTER_QUEUE_DEPTH: Gauge = Gauge(
    "hermes_dead_letter_queue_depth",
    "Current number of items in the in-memory dead-letter queue",
)

INFLIGHT_REQUESTS: Gauge = Gauge(
    "hermes_inflight_requests",
    "Number of webhook requests currently being processed",
)

DEAD_LETTER_QUEUE_ALERTS: Counter = Counter(
    "hermes_dead_letter_queue_alerts_total",
    "Number of times the dead-letter queue depth crossed the alert threshold",
)

DEAD_LETTER_EVICTIONS: Counter = Counter(
    "hermes_dead_letter_evictions_total",
    "Number of times the in-memory dead-letter deque evicted its oldest entry at capacity",
)
