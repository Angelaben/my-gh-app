"""Operational metrics — one JSONL record per AI operation.

Follows the same SAFETY CONTRACT as app.log_setup: only metadata (sizes,
counts, timings, identifiers) is recorded — never diff content, review text,
comment bodies, or raw AI output.

Records land in METRICS_FILE (default ``.cache/metrics.jsonl``); the file is
append-only and lives next to the JSON cache, so it never reaches the remote.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)

_PRIORITIES = ("P0", "P1", "P2", "P3")


def _metrics_path() -> Path:
    return Path(os.getenv("METRICS_FILE", ".cache/metrics.jsonl"))


def record(event: str, **fields: object) -> None:
    """Append one metrics record. Never raises — metrics must not break a run."""
    payload = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": event,
        **fields,
    }
    try:
        path = _metrics_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:
        logger.warning("metrics | write failed | error=%s", exc)


def file_token() -> tuple[int, int] | None:
    """Cheap change token for the metrics file: (mtime_ns, size), or None.

    Used by the /api/stats/stream SSE endpoint to detect new records without
    re-parsing the file on every poll tick.
    """
    try:
        st = _metrics_path().stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def load(limit: int | None = None) -> list[dict]:
    """Return parsed records, oldest first. Malformed lines are skipped."""
    path = _metrics_path()
    if not path.exists():
        return []
    records: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError as exc:
        logger.warning("metrics | read failed | error=%s", exc)
        return []
    if limit is not None:
        return records[-limit:]
    return records


def summarize() -> dict:
    """Aggregate all records for the /api/stats endpoint."""
    records = load()
    reviews = [r for r in records if r.get("event") == "review"]
    durations = [r["duration_ms"] for r in reviews if isinstance(r.get("duration_ms"), (int, float))]
    findings_by_priority = Counter()
    for r in reviews:
        for p in _PRIORITIES:
            count = r.get(f"findings_{p.lower()}")
            if isinstance(count, int):
                findings_by_priority[p] += count
    return {
        "total_records": len(records),
        "reviews": {
            "count": len(reviews),
            "avg_duration_ms": round(sum(durations) / len(durations)) if durations else None,
            "total_findings": sum(findings_by_priority.values()),
            "findings_by_priority": {p: findings_by_priority.get(p, 0) for p in _PRIORITIES},
            "by_provider": dict(Counter(
                str(r.get("provider")) for r in reviews if r.get("provider")
            )),
            "failed_chunks": sum(
                r.get("failed_chunks", 0) for r in reviews
                if isinstance(r.get("failed_chunks"), int)
            ),
        },
        "events": dict(Counter(str(r.get("event")) for r in records)),
        "recent": load(limit=20),
    }


def history(days: int = 14) -> dict:
    """Per-day review activity over the last ``days`` (for the Activity trends)."""
    days = max(1, min(int(days), 90))
    now = time.time()
    date_keys = [
        time.strftime("%Y-%m-%d", time.gmtime(now - i * 86400))
        for i in range(days - 1, -1, -1)
    ]
    index = {d: i for i, d in enumerate(date_keys)}
    reviews_per_day = [0] * days
    findings_per_day = [0] * days
    failed_per_day = [0] * days
    dur_sum = [0.0] * days
    dur_n = [0] * days
    findings_by_priority: Counter = Counter()
    by_provider: Counter = Counter()
    total = 0
    for r in load():
        if r.get("event") != "review":
            continue
        bucket = index.get(str(r.get("ts", ""))[:10])
        if bucket is None:
            continue
        total += 1
        reviews_per_day[bucket] += 1
        ft = r.get("findings_total")
        if isinstance(ft, int):
            findings_per_day[bucket] += ft
        duration = r.get("duration_ms")
        if isinstance(duration, (int, float)):
            dur_sum[bucket] += duration
            dur_n[bucket] += 1
        failed = r.get("failed_chunks")
        if isinstance(failed, int) and failed > 0:
            failed_per_day[bucket] += 1
        for p in _PRIORITIES:
            count = r.get(f"findings_{p.lower()}")
            if isinstance(count, int):
                findings_by_priority[p] += count
        if r.get("provider"):
            by_provider[str(r.get("provider"))] += 1
    total_dur = sum(dur_sum)
    total_dur_n = sum(dur_n)
    return {
        "days": days,
        "dates": date_keys,
        "reviews_per_day": reviews_per_day,
        "findings_per_day": findings_per_day,
        "failed_per_day": failed_per_day,
        "total_reviews": total,
        "findings_by_priority": {p: findings_by_priority.get(p, 0) for p in _PRIORITIES},
        "by_provider": dict(by_provider),
        "avg_duration_ms": round(total_dur / total_dur_n) if total_dur_n else None,
    }
