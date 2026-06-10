"""Local operation logging — never commit, never push.

Activated by setting LOG_OPS=1 (or any truthy value) in the environment.
Logs land in .logs/ops.jsonl as newline-delimited JSON, rotated at 10 MB
(5 backups kept).  The directory is gitignored so nothing ever reaches the
remote.

SAFETY CONTRACT — the following are NEVER written:
  * diff content (source code)
  * review text / finding descriptions
  * comment or PR bodies
  * raw AI output
Only operational metadata (sizes, timing, counts, exit codes) is recorded.
"""
from __future__ import annotations

import json
import logging
import os
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

_TRUTHY = {"1", "true", "yes", "on"}
LOG_OPS_ENABLED = os.getenv("LOG_OPS", "").strip().lower() in _TRUTHY

_LOG_DIR = Path(os.getenv("LOG_DIR", ".logs"))
_LOG_FILE = _LOG_DIR / "ops.jsonl"
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB per file
_BACKUP_COUNT = 5               # keep ops.jsonl … ops.jsonl.5


# Attributes present on every LogRecord — anything else came in via `extra=`
# and is emitted as a structured field (jq-queryable) alongside `msg`.
_STANDARD_ATTRS = frozenset(vars(logging.makeLogRecord({}))) | {
    "message", "asctime", "taskName",
}


class _JsonLineFormatter(logging.Formatter):
    """One compact JSON object per log record — grep- and jq-friendly.

    ``extra={...}`` fields passed to the logger are flattened into the JSON
    object so callers can emit queryable structured fields. The SAFETY
    CONTRACT above still applies to anything passed this way.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "ms": int(record.msecs),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None)
        if request_id and request_id != "-":
            payload["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS or key == "request_id" or key.startswith("_"):
                continue
            payload[key] = value
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup() -> None:
    """Attach a rotating JSONL file handler to the root logger.

    Idempotent: safe to call more than once (duplicate handlers are skipped).
    Must be called before the first log record is emitted to capture startup
    messages.
    """
    root = logging.getLogger()
    if any(getattr(h, "_log_ops_marker", False) for h in root.handlers):
        return  # already wired up

    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        _LOG_FILE,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler._log_ops_marker = True  # type: ignore[attr-defined]
    handler.setFormatter(_JsonLineFormatter())
    handler.setLevel(logging.DEBUG)
    root.addHandler(handler)
    logging.getLogger(__name__).info(
        "log_setup | active | path=%s max_bytes=%d backups=%d",
        _LOG_FILE, _MAX_BYTES, _BACKUP_COUNT,
    )
