"""In-memory ring buffer of recent log records, served by /api/logs.

Always active — keeps the last N records in memory only (nothing extra is
written to disk), so the UI's Activity page can show live backend activity
without requiring LOG_OPS. The SAFETY CONTRACT from :mod:`app.log_setup`
applies unchanged: records carry operational metadata only, never diff
content, review text, or raw AI output.

Records are dicts produced by :func:`app.log_setup.record_to_dict`, plus a
monotonic ``seq`` so clients (SSE stream, incremental fetch) can resume from
the last record they saw.
"""
from __future__ import annotations

import logging

from app.log_setup import record_to_dict

DEFAULT_CAPACITY = 500


class RingBufferHandler(logging.Handler):
    """Keeps the last ``capacity`` log records as dicts with a ``seq`` field.

    Reads and writes go through the Handler's own re-entrant ``self.lock``
    (``handle()`` already holds it while calling :meth:`emit`).
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        super().__init__(level=logging.INFO)
        self._capacity = capacity
        self._records: list[dict] = []
        self._next_seq = 1

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = record_to_dict(record)
            # JSON-safety up-front: values must survive json.dumps later.
            payload = {k: _json_safe(v) for k, v in payload.items()}
            payload["seq"] = self._next_seq
            self._next_seq += 1
            self._records.append(payload)
            if len(self._records) > self._capacity:
                del self._records[: len(self._records) - self._capacity]
        except Exception:  # pragma: no cover — logging must never raise
            self.handleError(record)

    @property
    def last_seq(self) -> int:
        self.acquire()
        try:
            return self._next_seq - 1
        finally:
            self.release()

    def records_after(self, after_seq: int = 0, limit: int = 200) -> list[dict]:
        """Records with seq > after_seq, oldest first, capped to the last `limit`."""
        self.acquire()
        try:
            items = [r for r in self._records if r["seq"] > after_seq]
        finally:
            self.release()
        if limit and len(items) > limit:
            items = items[-limit:]
        return items


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


_handler: RingBufferHandler | None = None


def install(root: logging.Logger | None = None) -> RingBufferHandler:
    """Create (once) and attach the shared ring buffer handler to the root logger."""
    global _handler
    if _handler is None:
        _handler = RingBufferHandler()
    target = root if root is not None else logging.getLogger()
    if _handler not in target.handlers:
        target.addHandler(_handler)
    return _handler


def get_handler() -> RingBufferHandler:
    """Return the shared handler, installing it on the root logger if needed."""
    return install()
