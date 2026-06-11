"""Shared pytest fixtures."""
import pytest


@pytest.fixture(autouse=True)
def _isolate_metrics_file(tmp_path, monkeypatch):
    """Keep test runs from appending to the real .cache/metrics.jsonl."""
    monkeypatch.setenv("METRICS_FILE", str(tmp_path / "metrics.jsonl"))
