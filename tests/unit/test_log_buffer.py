"""Tests for the in-memory log ring buffer behind /api/logs."""
import logging

from app.log_buffer import RingBufferHandler


def _make_logger(handler: RingBufferHandler, name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return logger


class TestRingBufferHandler:
    def test_records_carry_increasing_seq(self):
        handler = RingBufferHandler()
        logger = _make_logger(handler, "test.ring.seq")
        logger.info("one")
        logger.info("two")
        records = handler.records_after()
        assert [r["msg"] for r in records] == ["one", "two"]
        assert [r["seq"] for r in records] == [1, 2]
        assert handler.last_seq == 2

    def test_capacity_evicts_oldest(self):
        handler = RingBufferHandler(capacity=3)
        logger = _make_logger(handler, "test.ring.capacity")
        for i in range(5):
            logger.info("msg %d", i)
        records = handler.records_after()
        assert len(records) == 3
        assert [r["msg"] for r in records] == ["msg 2", "msg 3", "msg 4"]
        assert handler.last_seq == 5  # seq keeps counting past evictions

    def test_records_after_seq_and_limit(self):
        handler = RingBufferHandler()
        logger = _make_logger(handler, "test.ring.after")
        for i in range(10):
            logger.info("msg %d", i)
        after = handler.records_after(after_seq=7)
        assert [r["seq"] for r in after] == [8, 9, 10]
        limited = handler.records_after(limit=2)
        assert [r["seq"] for r in limited] == [9, 10]

    def test_extra_fields_flattened_and_json_safe(self):
        handler = RingBufferHandler()
        logger = _make_logger(handler, "test.ring.extra")
        logger.info("op", extra={"repo": "acme/backend", "payload": object()})
        record = handler.records_after()[-1]
        assert record["repo"] == "acme/backend"
        assert isinstance(record["payload"], str)
        assert record["level"] == "INFO"
        assert record["logger"] == "test.ring.extra"

    def test_debug_records_are_filtered_out(self):
        handler = RingBufferHandler()
        logger = _make_logger(handler, "test.ring.level")
        logger.debug("hidden")
        logger.info("visible")
        assert [r["msg"] for r in handler.records_after()] == ["visible"]
