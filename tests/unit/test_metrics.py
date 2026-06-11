"""Tests for the metrics JSONL store."""
import json
from pathlib import Path

import pytest

from app.services import metrics


@pytest.fixture
def metrics_file(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "metrics.jsonl"
    monkeypatch.setenv("METRICS_FILE", str(path))
    return path


class TestRecord:
    def test_appends_one_json_line(self, metrics_file: Path):
        metrics.record("review", repo="acme/backend", pr=1, duration_ms=1200)
        lines = metrics_file.read_text().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["event"] == "review"
        assert data["repo"] == "acme/backend"
        assert data["duration_ms"] == 1200
        assert "ts" in data

    def test_never_raises_on_unwritable_path(self, monkeypatch):
        monkeypatch.setenv("METRICS_FILE", "/proc/does-not-exist/metrics.jsonl")
        metrics.record("review", repo="acme/backend")  # must not raise

    def test_serializes_non_json_values_as_strings(self, metrics_file: Path):
        metrics.record("review", model=object())
        data = json.loads(metrics_file.read_text())
        assert isinstance(data["model"], str)


class TestFileToken:
    def test_none_when_missing(self, metrics_file: Path):
        assert metrics.file_token() is None

    def test_changes_after_record(self, metrics_file: Path):
        metrics.record("review", pr=1)
        first = metrics.file_token()
        assert first is not None
        metrics.record("review", pr=2)
        assert metrics.file_token() != first


class TestLoad:
    def test_empty_when_file_missing(self, metrics_file: Path):
        assert metrics.load() == []

    def test_skips_malformed_lines(self, metrics_file: Path):
        metrics.record("review", pr=1)
        metrics_file.write_text(metrics_file.read_text() + "not-json\n")
        metrics.record("review", pr=2)
        records = metrics.load()
        assert [r["pr"] for r in records] == [1, 2]

    def test_limit_returns_most_recent(self, metrics_file: Path):
        for i in range(5):
            metrics.record("review", pr=i)
        records = metrics.load(limit=2)
        assert [r["pr"] for r in records] == [3, 4]


class TestSummarize:
    def test_empty_store(self, metrics_file: Path):
        summary = metrics.summarize()
        assert summary["reviews"]["count"] == 0
        assert summary["reviews"]["avg_duration_ms"] is None

    def test_aggregates_reviews(self, metrics_file: Path):
        metrics.record(
            "review", provider="opencode", duration_ms=1000,
            findings_p0=1, findings_p1=2, findings_p2=0, findings_p3=0,
            failed_chunks=0,
        )
        metrics.record(
            "review", provider="claude-code", duration_ms=3000,
            findings_p0=0, findings_p1=1, findings_p2=3, findings_p3=1,
            failed_chunks=2,
        )
        summary = metrics.summarize()
        reviews = summary["reviews"]
        assert reviews["count"] == 2
        assert reviews["avg_duration_ms"] == 2000
        assert reviews["findings_by_priority"] == {"P0": 1, "P1": 3, "P2": 3, "P3": 1}
        assert reviews["total_findings"] == 8
        assert reviews["by_provider"] == {"opencode": 1, "claude-code": 1}
        assert reviews["failed_chunks"] == 2
