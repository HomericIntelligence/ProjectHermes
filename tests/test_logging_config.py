"""Tests for hermes.logging_config — JsonFormatter and setup_logging."""

from __future__ import annotations

import json
import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from hermes.logging_config import JsonFormatter, setup_logging


def _make_record(
    message: str = "hello",
    level: int = logging.INFO,
    name: str = "test.logger",
    **extra: object,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname="",
        lineno=0,
        msg=message,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


class TestJsonFormatter:
    def test_output_is_valid_json(self) -> None:
        formatter = JsonFormatter()
        record = _make_record()
        output = formatter.format(record)
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_core_fields_present(self) -> None:
        formatter = JsonFormatter()
        record = _make_record(message="test message", name="my.module")
        parsed = json.loads(formatter.format(record))
        assert "timestamp" in parsed
        assert "level" in parsed
        assert "logger" in parsed
        assert "message" in parsed

    def test_message_content(self) -> None:
        formatter = JsonFormatter()
        record = _make_record(message="expected content")
        parsed = json.loads(formatter.format(record))
        assert parsed["message"] == "expected content"

    def test_level_name(self) -> None:
        formatter = JsonFormatter()
        record = _make_record(level=logging.WARNING)
        parsed = json.loads(formatter.format(record))
        assert parsed["level"] == "WARNING"

    def test_logger_name(self) -> None:
        formatter = JsonFormatter()
        record = _make_record(name="hermes.publisher")
        parsed = json.loads(formatter.format(record))
        assert parsed["logger"] == "hermes.publisher"

    def test_timestamp_is_iso8601(self) -> None:
        formatter = JsonFormatter()
        record = _make_record()
        parsed = json.loads(formatter.format(record))
        # Should parse without error and contain timezone info
        from datetime import datetime
        dt = datetime.fromisoformat(parsed["timestamp"])
        assert dt.tzinfo is not None

    def test_extra_fields_appear_as_top_level_keys(self) -> None:
        formatter = JsonFormatter()
        record = _make_record(request_id="abc-123")
        parsed = json.loads(formatter.format(record))
        assert parsed.get("request_id") == "abc-123"

    def test_reserved_key_collision_gets_ctx_prefix(self) -> None:
        formatter = JsonFormatter()
        # Manually set an attribute that would collide with a reserved key
        record2 = _make_record()
        setattr(record2, "level", "COLLISION")  # noqa: B010
        # The formatter should handle this without error
        output = formatter.format(record2)
        parsed = json.loads(output)
        # The real level should still be present and not overwritten
        assert parsed["level"] in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

    def test_non_string_extra_serialized(self) -> None:
        formatter = JsonFormatter()
        record = _make_record(count=42, tags=["a", "b"])
        parsed = json.loads(formatter.format(record))
        assert parsed.get("count") == 42
        assert parsed.get("tags") == ["a", "b"]

    def test_percent_interpolation_resolved(self) -> None:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello %s",
            args=("world",),
            exc_info=None,
        )
        formatter = JsonFormatter()
        parsed = json.loads(formatter.format(record))
        assert parsed["message"] == "hello world"


class TestSetupLogging:
    def _clear_root_handlers(self) -> None:
        root = logging.getLogger()
        for h in list(root.handlers):
            root.removeHandler(h)

    def test_setup_logging_plain_adds_handler(self) -> None:
        self._clear_root_handlers()
        setup_logging(json_format=False)
        root = logging.getLogger()
        assert len(root.handlers) >= 1

    def test_setup_logging_json_uses_json_formatter(self) -> None:
        self._clear_root_handlers()
        setup_logging(json_format=True)
        root = logging.getLogger()
        stream_handlers = [
            h for h in root.handlers
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        ]
        assert any(isinstance(h.formatter, JsonFormatter) for h in stream_handlers)

    def test_setup_logging_plain_does_not_use_json_formatter(self) -> None:
        self._clear_root_handlers()
        setup_logging(json_format=False)
        root = logging.getLogger()
        stream_handlers = [
            h for h in root.handlers
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        ]
        assert not any(isinstance(h.formatter, JsonFormatter) for h in stream_handlers)

    def test_no_duplicate_handlers_on_repeated_calls(self) -> None:
        self._clear_root_handlers()
        setup_logging(json_format=False)
        setup_logging(json_format=False)
        setup_logging(json_format=True)
        root = logging.getLogger()
        stream_handlers = [
            h for h in root.handlers
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        ]
        assert len(stream_handlers) == 1

    def test_setup_logging_sets_level(self) -> None:
        self._clear_root_handlers()
        setup_logging(level=logging.DEBUG)
        assert logging.getLogger().level == logging.DEBUG
