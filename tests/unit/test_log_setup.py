"""Tests for the JSONL log formatter."""
import json
import logging

from app.log_setup import _JsonLineFormatter


def _format(record: logging.LogRecord) -> dict:
    return json.loads(_JsonLineFormatter().format(record))


def _make_record(msg: str = "hello", **extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg=msg, args=(), exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


class TestJsonLineFormatter:
    def test_base_fields(self):
        data = _format(_make_record("hello"))
        assert data["msg"] == "hello"
        assert data["level"] == "INFO"
        assert data["logger"] == "test"
        assert "ts" in data

    def test_extra_fields_are_flattened(self):
        data = _format(_make_record("op", repo="acme/backend", duration_ms=42))
        assert data["repo"] == "acme/backend"
        assert data["duration_ms"] == 42

    def test_request_id_included_when_set(self):
        data = _format(_make_record("op", request_id="ab12cd34"))
        assert data["request_id"] == "ab12cd34"

    def test_request_id_omitted_when_placeholder(self):
        data = _format(_make_record("op", request_id="-"))
        assert "request_id" not in data

    def test_non_json_extra_values_are_stringified(self):
        data = _format(_make_record("op", payload=object()))
        assert isinstance(data["payload"], str)
