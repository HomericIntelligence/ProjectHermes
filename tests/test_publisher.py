"""Tests for NATS subject routing logic in hermes.publisher."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import nats.errors
import pytest

from nats.js.errors import NotFoundError

from hermes.models import WebhookPayload
from hermes.publisher import Publisher, UnknownEventTypeError, _RETRYABLE_PUBLISH_ERRORS, _slug


def _make_publisher() -> Publisher:
    return Publisher()


class TestAgentSubjectMapping:
    """Agent event → NATS subject routing."""

    def test_agent_created(self) -> None:
        pub = _make_publisher()
        subject = pub._parse_agent_subject(
            {"host": "docker-desktop", "name": "researcher"},
            "agent.created",
        )
        assert subject == "hi.agents.docker-desktop.researcher.created"

    def test_agent_updated(self) -> None:
        pub = _make_publisher()
        subject = pub._parse_agent_subject(
            {"host": "worker-01", "name": "analyst"},
            "agent.updated",
        )
        assert subject == "hi.agents.worker-01.analyst.updated"

    def test_agent_deleted(self) -> None:
        pub = _make_publisher()
        subject = pub._parse_agent_subject(
            {"host": "worker-01", "name": "scout"},
            "agent.deleted",
        )
        assert subject == "hi.agents.worker-01.scout.deleted"

    def test_missing_host_falls_back_to_unknown(self) -> None:
        pub = _make_publisher()
        subject = pub._parse_agent_subject({"name": "bot"}, "agent.created")
        assert subject == "hi.agents.unknown.bot.created"

    def test_missing_name_falls_back_to_unknown(self) -> None:
        pub = _make_publisher()
        subject = pub._parse_agent_subject({"host": "myhost"}, "agent.created")
        assert subject == "hi.agents.myhost.unknown.created"

    def test_spaces_in_tokens_are_slugified(self) -> None:
        pub = _make_publisher()
        subject = pub._parse_agent_subject(
            {"host": "my host", "name": "my agent"},
            "agent.created",
        )
        assert " " not in subject
        assert subject == "hi.agents.my-host.my-agent.created"

    def test_host_id_field_used_as_host(self) -> None:
        pub = _make_publisher()
        payload = WebhookPayload(
            event="agent.created",
            data={"hostId": "myhost", "name": "bot"},
            timestamp="2026-01-01T00:00:00Z",
        )
        subject = pub._resolve_subject(payload)
        assert subject == "hi.agents.myhost.bot.created"

    def test_host_id_with_wildcard_is_sanitized(self) -> None:
        pub = _make_publisher()
        payload = WebhookPayload(
            event="agent.created",
            data={"hostId": "bad*host", "name": "bot"},
            timestamp="2026-01-01T00:00:00Z",
        )
        subject = pub._resolve_subject(payload)
        assert subject is not None
        assert "*" not in subject
        assert ">" not in subject

    def test_whitespace_only_host_falls_back_to_unknown(self) -> None:
        pub = _make_publisher()
        subject = pub._parse_agent_subject({"host": "   ", "name": "bot"}, "agent.created")
        assert subject == "hi.agents.unknown.bot.created"

    def test_whitespace_only_name_falls_back_to_unknown(self) -> None:
        pub = _make_publisher()
        subject = pub._parse_agent_subject({"host": "myhost", "name": "   "}, "agent.created")
        assert subject == "hi.agents.myhost.unknown.created"


class TestTaskSubjectMapping:
    """Task event → NATS subject routing."""

    def test_task_updated(self) -> None:
        pub = _make_publisher()
        subject = pub._parse_task_subject(
            {"team_id": "team-alpha", "task_id": "task-42"},
            "task.updated",
        )
        assert subject == "hi.tasks.team-alpha.task-42.updated"

    def test_task_completed(self) -> None:
        pub = _make_publisher()
        subject = pub._parse_task_subject(
            {"team_id": "team-beta", "task_id": "t-99"},
            "task.completed",
        )
        assert subject == "hi.tasks.team-beta.t-99.completed"

    def test_missing_team_id_falls_back_to_unknown(self) -> None:
        pub = _make_publisher()
        subject = pub._parse_task_subject({"task_id": "t-1"}, "task.updated")
        assert subject == "hi.tasks.unknown.t-1.updated"

    def test_alternate_id_key(self) -> None:
        """task_id falls back to 'id' if 'task_id' is absent."""
        pub = _make_publisher()
        subject = pub._parse_task_subject(
            {"team_id": "alpha", "id": "xyz"},
            "task.updated",
        )
        assert subject == "hi.tasks.alpha.xyz.updated"


class TestEnsureStreams:
    """_ensure_streams stream creation logic."""

    def _make_connected_publisher(self) -> Publisher:
        pub = Publisher()
        pub._nc = MagicMock()
        return pub

    @pytest.mark.asyncio
    async def test_stream_exists_no_create(self) -> None:
        pub = self._make_connected_publisher()
        jsm = AsyncMock()
        jsm.stream_info = AsyncMock(return_value=MagicMock())
        jsm.add_stream = AsyncMock()
        pub._nc.jsm.return_value = jsm

        await pub._ensure_streams()

        assert jsm.add_stream.call_count == 0

    @pytest.mark.asyncio
    async def test_stream_not_found_creates_stream(self) -> None:
        pub = self._make_connected_publisher()
        jsm = AsyncMock()
        jsm.stream_info = AsyncMock(side_effect=NotFoundError)
        jsm.add_stream = AsyncMock()
        pub._nc.jsm.return_value = jsm

        await pub._ensure_streams()

        assert jsm.add_stream.call_count == 3  # agents + tasks + deadletter
        names = {call.args[0].name for call in jsm.add_stream.call_args_list}
        assert names == {"homeric-agents", "homeric-tasks", "homeric-deadletter"}

    @pytest.mark.asyncio
    async def test_stream_names_no_duplicates_on_repeated_ensure(self) -> None:
        pub = self._make_connected_publisher()
        jsm = AsyncMock()
        jsm.stream_info = AsyncMock(return_value=MagicMock())
        jsm.add_stream = AsyncMock()
        pub._nc.jsm.return_value = jsm

        await pub._ensure_streams()
        initial_count = len(pub._stream_names)
        await pub._ensure_streams()

        assert len(pub._stream_names) == initial_count

    @pytest.mark.asyncio
    async def test_non_notfounderror_propagates(self) -> None:
        pub = self._make_connected_publisher()
        jsm = AsyncMock()
        jsm.stream_info = AsyncMock(side_effect=OSError("connection refused"))
        jsm.add_stream = AsyncMock()
        pub._nc.jsm.return_value = jsm

        with pytest.raises(OSError, match="connection refused"):
            await pub._ensure_streams()

        assert jsm.add_stream.call_count == 0


class TestSlugSanitisation:
    """Unit tests for the _slug() helper covering wildcard sanitisation."""

    def test_whitespace_only_returns_empty_string(self) -> None:
        assert _slug("   ") == ""

    def test_wildcard_star_is_removed(self) -> None:
        assert "*" not in _slug("test*")

    def test_wildcard_gt_is_removed(self) -> None:
        assert ">" not in _slug("all>")

    def test_wildcards_mixed_with_spaces(self) -> None:
        result = _slug("my *agent >")
        assert "*" not in result
        assert ">" not in result
        assert result == "my-agent-"

    def test_wildcard_star_full_subject(self) -> None:
        pub = _make_publisher()
        subject = pub._parse_agent_subject(
            {"host": "myhost", "name": "test*"},
            "agent.created",
        )
        assert "*" not in subject
        assert subject == "hi.agents.myhost.test.created"

    def test_wildcard_gt_full_subject(self) -> None:
        pub = _make_publisher()
        subject = pub._parse_agent_subject(
            {"host": "myhost", "name": "all>"},
            "agent.created",
        )
        assert ">" not in subject
        assert subject == "hi.agents.myhost.all.created"

    def test_all_wildcards_fall_back_to_unknown(self) -> None:
        pub = _make_publisher()
        payload = WebhookPayload(
            event="agent.created",
            data={"host": "***", "name": ">>>"},
            timestamp="2026-04-23T00:00:00Z",
        )
        subject = pub._resolve_subject(payload)
        assert subject is not None
        parts = subject.split(".")
        assert all(p for p in parts), f"Empty token in subject: {subject}"
        assert "unknown" in subject

    def test_all_wildcards_task_falls_back_to_unknown(self) -> None:
        pub = _make_publisher()
        payload = WebhookPayload(
            event="task.updated",
            data={"team_id": "***", "task_id": ">>>"},
            timestamp="2026-04-23T00:00:00Z",
        )
        subject = pub._resolve_subject(payload)
        assert subject is not None
        parts = subject.split(".")
        assert all(p for p in parts), f"Empty token in subject: {subject}"
        assert "unknown" in subject


class TestActiveSubjectsBound:
    """_active_subjects LRU bound behaviour."""

    def _add_subject(self, pub: Publisher, subject: str) -> None:
        """Directly insert a subject as if it had been published."""
        pub._track_subject(subject)

    def test_subjects_below_cap_are_all_retained(self) -> None:
        pub = Publisher(max_subjects=5)
        for i in range(5):
            self._add_subject(pub, f"hi.tasks.team.task-{i}.updated")
        assert len(pub.active_subjects) == 5

    def test_oldest_evicted_when_cap_exceeded(self) -> None:
        pub = Publisher(max_subjects=3)
        for i in range(4):
            self._add_subject(pub, f"hi.tasks.team.task-{i}.updated")
        subjects = pub.active_subjects
        assert len(subjects) == 3
        assert "hi.tasks.team.task-0.updated" not in subjects

    def test_republishing_existing_subject_does_not_evict(self) -> None:
        pub = Publisher(max_subjects=3)
        subjects_added = [f"hi.tasks.team.task-{i}.updated" for i in range(3)]
        for s in subjects_added:
            self._add_subject(pub, s)
        self._add_subject(pub, subjects_added[0])
        assert len(pub.active_subjects) == 3
        assert subjects_added[0] in pub.active_subjects

    def test_lru_promotes_on_repeat_and_evicts_next_oldest(self) -> None:
        pub = Publisher(max_subjects=3)
        s0, s1, s2 = "a", "b", "c"
        for s in (s0, s1, s2):
            self._add_subject(pub, s)
        self._add_subject(pub, s0)
        self._add_subject(pub, "d")
        subjects = pub.active_subjects
        assert s1 not in subjects
        assert s0 in subjects

    def test_active_subjects_returns_sorted_list(self) -> None:
        pub = Publisher(max_subjects=10)
        for s in ("z.subject", "a.subject", "m.subject"):
            self._add_subject(pub, s)
        assert pub.active_subjects == sorted(["z.subject", "a.subject", "m.subject"])

    def test_custom_max_subjects_respected(self) -> None:
        pub = Publisher(max_subjects=2)
        assert pub._max_subjects == 2
        for i in range(5):
            self._add_subject(pub, f"subj-{i}")
        assert len(pub.active_subjects) == 2


def _agent_payload() -> WebhookPayload:
    return WebhookPayload(
        event="agent.created",
        data={"host": "myhost", "name": "myagent"},
        timestamp="2026-04-24T00:00:00Z",
    )


def _make_connected_publisher() -> Publisher:
    pub = Publisher()
    pub._js = AsyncMock()
    pub._connected = True
    pub._nc = MagicMock()
    return pub


class TestPublishRetry:
    """Retry logic for transient NATS publish failures."""

    @pytest.mark.asyncio
    async def test_succeeds_on_second_attempt_after_transient_failure(self) -> None:
        pub = _make_connected_publisher()
        pub._js.publish = AsyncMock(side_effect=[nats.errors.TimeoutError(), MagicMock()])

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await pub.publish(_agent_payload(), publish_retries=3, publish_retry_base_delay=0.1)

        assert pub._js.publish.call_count == 2
        mock_sleep.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_retryable_error_is_not_retried(self) -> None:
        pub = _make_connected_publisher()
        pub._js.publish = AsyncMock(side_effect=ValueError("bad event"))

        with pytest.raises(ValueError, match="bad event"):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await pub.publish(_agent_payload(), publish_retries=3, publish_retry_base_delay=0.1)

        assert pub._js.publish.call_count == 1
        mock_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_retry_count_is_respected(self) -> None:
        pub = _make_connected_publisher()
        pub._js.publish = AsyncMock(side_effect=nats.errors.NoRespondersError())

        with pytest.raises(nats.errors.NoRespondersError):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                await pub.publish(_agent_payload(), publish_retries=3, publish_retry_base_delay=0.1)

        assert pub._js.publish.call_count == 3


class TestUnknownEventTypeError:
    """Publisher raises UnknownEventTypeError for unknown events when dead-lettering is off."""

    @pytest.mark.asyncio
    async def test_unknown_event_raises_when_dead_letter_disabled(self) -> None:
        from hermes.models import WebhookPayload

        pub = Publisher(enable_dead_letter=False)
        pub._js = AsyncMock()

        payload = WebhookPayload(event="foo.unknown", data={}, timestamp="2026-01-01T00:00:00Z")
        with pytest.raises(UnknownEventTypeError):
            await pub.publish(payload)

    @pytest.mark.asyncio
    async def test_unknown_event_error_message_contains_event_type(self) -> None:
        from hermes.models import WebhookPayload

        pub = Publisher(enable_dead_letter=False)
        pub._js = AsyncMock()

        payload = WebhookPayload(event="foo.unknown", data={}, timestamp="2026-01-01T00:00:00Z")
        with pytest.raises(UnknownEventTypeError, match="foo.unknown"):
            await pub.publish(payload)


class TestRetryJitter:
    """Jitter is applied to exponential backoff delays (issue #211)."""

    @pytest.mark.asyncio
    async def test_jitter_applied_to_sleep_delay(self) -> None:
        """asyncio.sleep receives a value scaled by random.uniform(0.5, 1.5)."""
        pub = _make_connected_publisher()
        pub._js.publish = AsyncMock(side_effect=[nats.errors.TimeoutError(), MagicMock()])

        with (
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("random.uniform", return_value=1.2) as mock_uniform,
        ):
            await pub.publish(_agent_payload(), publish_retries=3, publish_retry_base_delay=0.1)

        mock_uniform.assert_called_once_with(0.5, 1.5)
        # base_delay * 2^0 * jitter = 0.1 * 1 * 1.2 = 0.12
        mock_sleep.assert_awaited_once_with(pytest.approx(0.12, rel=1e-3))

    @pytest.mark.asyncio
    async def test_jitter_lower_bound(self) -> None:
        """Sleep delay is at least base_delay * 2^attempt * 0.5."""
        pub = _make_connected_publisher()
        pub._js.publish = AsyncMock(side_effect=[nats.errors.TimeoutError(), MagicMock()])

        with (
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("random.uniform", return_value=0.5),
        ):
            await pub.publish(_agent_payload(), publish_retries=3, publish_retry_base_delay=0.1)

        # 0.1 * 2^0 * 0.5 = 0.05
        mock_sleep.assert_awaited_once_with(pytest.approx(0.05, rel=1e-3))

    @pytest.mark.asyncio
    async def test_jitter_upper_bound(self) -> None:
        """Sleep delay is at most min(base_delay * 2^attempt, 2.0) * 1.5."""
        pub = _make_connected_publisher()
        pub._js.publish = AsyncMock(side_effect=[nats.errors.TimeoutError(), MagicMock()])

        with (
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("random.uniform", return_value=1.5),
        ):
            await pub.publish(_agent_payload(), publish_retries=3, publish_retry_base_delay=0.1)

        # 0.1 * 2^0 * 1.5 = 0.15
        mock_sleep.assert_awaited_once_with(pytest.approx(0.15, rel=1e-3))

    @pytest.mark.asyncio
    async def test_delay_cap_applied_before_jitter(self) -> None:
        """The 2.0-second cap is applied before multiplying by jitter."""
        pub = _make_connected_publisher()
        pub._js.publish = AsyncMock(
            side_effect=[nats.errors.TimeoutError(), nats.errors.TimeoutError(), MagicMock()]
        )

        with (
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("random.uniform", return_value=1.0),
        ):
            # base_delay=10.0 so base*2^1=20 → capped at 2.0; * 1.0 jitter = 2.0
            await pub.publish(_agent_payload(), publish_retries=3, publish_retry_base_delay=10.0)

        sleep_calls = [call.args[0] for call in mock_sleep.await_args_list]
        assert all(v <= 2.0 * 1.5 for v in sleep_calls), f"Unexpected large delay: {sleep_calls}"


class TestRetryableExceptions:
    """Only retryable exceptions are retried; non-retryable ones propagate immediately (issue #212)."""

    def test_retryable_tuple_contains_timeout_error(self) -> None:
        assert nats.errors.TimeoutError in _RETRYABLE_PUBLISH_ERRORS

    def test_retryable_tuple_contains_no_responders_error(self) -> None:
        assert nats.errors.NoRespondersError in _RETRYABLE_PUBLISH_ERRORS

    def test_retryable_tuple_contains_drain_timeout_error(self) -> None:
        assert nats.errors.DrainTimeoutError in _RETRYABLE_PUBLISH_ERRORS

    def test_retryable_tuple_contains_connection_reconnecting_error(self) -> None:
        """ConnectionReconnectingError is retryable (issue #74: reconnection race)."""
        assert nats.errors.ConnectionReconnectingError in _RETRYABLE_PUBLISH_ERRORS

    def test_retryable_tuple_contains_stale_connection_error(self) -> None:
        """StaleConnectionError is retryable (issue #74: reconnection race)."""
        assert nats.errors.StaleConnectionError in _RETRYABLE_PUBLISH_ERRORS

    @pytest.mark.asyncio
    async def test_drain_timeout_error_is_retried(self) -> None:
        pub = _make_connected_publisher()
        pub._js.publish = AsyncMock(side_effect=[nats.errors.DrainTimeoutError(), MagicMock()])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await pub.publish(_agent_payload(), publish_retries=3, publish_retry_base_delay=0.01)

        assert pub._js.publish.call_count == 2

    @pytest.mark.asyncio
    async def test_authorization_error_is_not_retried(self) -> None:
        """AuthorizationError is not in _RETRYABLE_PUBLISH_ERRORS and must not be retried."""
        pub = _make_connected_publisher()
        pub._js.publish = AsyncMock(side_effect=nats.errors.AuthorizationError())

        with pytest.raises(nats.errors.AuthorizationError):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await pub.publish(
                    _agent_payload(), publish_retries=3, publish_retry_base_delay=0.01
                )

        assert pub._js.publish.call_count == 1
        mock_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_connection_closed_error_is_not_retried(self) -> None:
        """ConnectionClosedError is not retryable and must propagate on first attempt."""
        pub = _make_connected_publisher()
        pub._js.publish = AsyncMock(side_effect=nats.errors.ConnectionClosedError())

        with pytest.raises(nats.errors.ConnectionClosedError):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await pub.publish(
                    _agent_payload(), publish_retries=3, publish_retry_base_delay=0.01
                )

        assert pub._js.publish.call_count == 1
        mock_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_all_retryable_errors_exhaust_retry_budget(self) -> None:
        """Each retryable error type exhausts the full retry budget."""
        for error_cls in _RETRYABLE_PUBLISH_ERRORS:
            pub = _make_connected_publisher()
            pub._js.publish = AsyncMock(side_effect=error_cls())

            with pytest.raises(error_cls):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    await pub.publish(
                        _agent_payload(), publish_retries=2, publish_retry_base_delay=0.01
                    )

            assert pub._js.publish.call_count == 2, f"Expected 2 attempts for {error_cls}"


class TestReconnectRace:
    """Issue #74: ConnectionReconnectingError / StaleConnectionError are retried.

    When NATS loses its connection between the is_connected check in server.py and
    the actual publish call, nats-py raises ConnectionReconnectingError or
    StaleConnectionError.  These are transient and should be retried.
    """

    @pytest.mark.asyncio
    async def test_connection_reconnecting_error_is_retried(self) -> None:
        pub = _make_connected_publisher()
        pub._js.publish = AsyncMock(
            side_effect=[nats.errors.ConnectionReconnectingError(), MagicMock()]
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await pub.publish(_agent_payload(), publish_retries=3, publish_retry_base_delay=0.01)

        assert pub._js.publish.call_count == 2

    @pytest.mark.asyncio
    async def test_stale_connection_error_is_retried(self) -> None:
        pub = _make_connected_publisher()
        pub._js.publish = AsyncMock(side_effect=[nats.errors.StaleConnectionError(), MagicMock()])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await pub.publish(_agent_payload(), publish_retries=3, publish_retry_base_delay=0.01)

        assert pub._js.publish.call_count == 2

    @pytest.mark.asyncio
    async def test_reconnecting_error_exhausts_retry_budget(self) -> None:
        pub = _make_connected_publisher()
        pub._js.publish = AsyncMock(side_effect=nats.errors.ConnectionReconnectingError())

        with pytest.raises(nats.errors.ConnectionReconnectingError):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                await pub.publish(
                    _agent_payload(), publish_retries=3, publish_retry_base_delay=0.01
                )

        assert pub._js.publish.call_count == 3

    @pytest.mark.asyncio
    async def test_connection_closed_error_is_not_retried(self) -> None:
        """ConnectionClosedError is distinct from reconnecting and must not be retried."""
        pub = _make_connected_publisher()
        pub._js.publish = AsyncMock(side_effect=nats.errors.ConnectionClosedError())

        with pytest.raises(nats.errors.ConnectionClosedError):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await pub.publish(
                    _agent_payload(), publish_retries=3, publish_retry_base_delay=0.01
                )

        assert pub._js.publish.call_count == 1
        mock_sleep.assert_not_awaited()


class TestTLSPublisherConnect:
    """Issue #185: TLS publisher connect tests must not generate RuntimeWarnings.

    When build_ssl_context() returns an SSLContext, Publisher.connect() passes it
    to nats.connect().  Ensure the mock is properly awaited and no RuntimeWarning
    (unawaited coroutine) is emitted.
    """

    @pytest.mark.asyncio
    async def test_connect_with_tls_ssl_context_no_runtime_warning(self) -> None:
        """Connecting with a TLS SSL context does not generate a RuntimeWarning."""
        import warnings

        pub = Publisher()
        mock_nc = MagicMock()
        mock_nc.is_closed = False
        mock_nc.jetstream.return_value = MagicMock()
        jsm = AsyncMock()
        jsm.stream_info = AsyncMock(return_value=MagicMock())
        jsm.add_stream = AsyncMock()
        mock_nc.jsm.return_value = jsm

        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            with patch("nats.connect", return_value=mock_nc) as mock_connect:
                await pub.connect("tls://localhost:4222")
                mock_connect.assert_called_once()

        assert pub.is_connected is True

    @pytest.mark.asyncio
    async def test_connect_omits_tls_kwarg_when_ssl_not_configured(self) -> None:
        """Publisher.connect() omits the tls kwarg when no TLS settings are configured."""
        pub = Publisher()
        mock_nc = MagicMock()
        mock_nc.jetstream.return_value = MagicMock()
        jsm = AsyncMock()
        jsm.stream_info = AsyncMock(return_value=MagicMock())
        jsm.add_stream = AsyncMock()
        mock_nc.jsm.return_value = jsm

        captured_kwargs: dict = {}

        async def fake_connect(url: str, **kwargs: object) -> MagicMock:
            captured_kwargs.update(kwargs)
            return mock_nc

        with patch("nats.connect", side_effect=fake_connect):
            await pub.connect("nats://localhost:4222")

        # Without TLS settings the ssl kwarg is absent (no ssl_context passed)
        assert "tls" not in captured_kwargs
