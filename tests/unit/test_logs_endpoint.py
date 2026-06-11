"""Tests for the /api/logs and /api/stats endpoints (Activity page backend)."""
import logging

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)

# Under pytest the root logger level is not main.py's basicConfig level, so
# pin this logger's level explicitly for the INFO probes below.
_probe_logger = logging.getLogger("test.endpoint")
_probe_logger.setLevel(logging.INFO)


class TestRecentLogs:
    def test_returns_records_and_last_seq(self):
        _probe_logger.info("ring buffer probe")
        resp = client.get("/api/logs/recent")
        assert resp.status_code == 200
        data = resp.json()
        assert "records" in data and "last_seq" in data
        assert any(r["msg"] == "ring buffer probe" for r in data["records"])
        assert data["last_seq"] >= data["records"][-1]["seq"]

    def test_after_seq_is_incremental(self):
        _probe_logger.info("first")
        first = client.get("/api/logs/recent").json()
        _probe_logger.info("second")
        delta = client.get(f"/api/logs/recent?after_seq={first['last_seq']}").json()
        assert all(r["seq"] > first["last_seq"] for r in delta["records"])
        assert any(r["msg"] == "second" for r in delta["records"])


class TestStats:
    def test_stats_endpoint_shape(self):
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "reviews" in data
        assert "findings_by_priority" in data["reviews"]
