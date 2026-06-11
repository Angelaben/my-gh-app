"""Tests for runtime AI provider switching: SwitchableAIProvider + endpoints."""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import SwitchableAIProvider, app
from app.ports.ai_provider import AIProvider, FixChunkEvent, ReviewChunkEvent


# --- SwitchableAIProvider ---


class _FakeAIProvider(AIProvider):
    """Minimal AIProvider for testing the switchable wrapper."""

    def __init__(self, tag: str) -> None:
        self.tag = tag

    async def stream_review(self, repo_full_name, pr_number, diff, model=None):
        yield ReviewChunkEvent(text=f"{self.tag}:review")

    async def analyze_comments(self, repo_full_name, pr_number, comments):
        return [{"tag": self.tag, "kind": "analyze"}]

    async def stream_fix(self, repo_dir, repo_full_name, pr_number, comment_body):
        yield FixChunkEvent(text=f"{self.tag}:fix")

    async def generate_text(self, prompt, timeout=60):
        return f"{self.tag}:text"


@pytest.fixture
def two_providers():
    return {"opencode": _FakeAIProvider("OC"), "claude-code": _FakeAIProvider("CC")}


def test_switchable_initial_active(two_providers):
    sw = SwitchableAIProvider(two_providers, active="opencode")
    assert sw.active == "opencode"


def test_switchable_unknown_initial_raises(two_providers):
    with pytest.raises(ValueError, match="Unknown active provider"):
        SwitchableAIProvider(two_providers, active="bogus")


def test_switchable_set_active(two_providers):
    sw = SwitchableAIProvider(two_providers, active="opencode")
    sw.set_active("claude-code")
    assert sw.active == "claude-code"


def test_switchable_set_active_unknown_raises(two_providers):
    sw = SwitchableAIProvider(two_providers, active="opencode")
    with pytest.raises(ValueError, match="Unknown provider 'bogus'"):
        sw.set_active("bogus")


@pytest.mark.asyncio
async def test_switchable_delegates_review(two_providers):
    sw = SwitchableAIProvider(two_providers, active="opencode")
    chunks = [e.text async for e in sw.stream_review("o/r", 1, "diff")]
    assert chunks == ["OC:review"]
    sw.set_active("claude-code")
    chunks = [e.text async for e in sw.stream_review("o/r", 1, "diff")]
    assert chunks == ["CC:review"]


@pytest.mark.asyncio
async def test_switchable_delegates_analyze(two_providers):
    sw = SwitchableAIProvider(two_providers, active="claude-code")
    out = await sw.analyze_comments("o/r", 1, [])
    assert out == [{"tag": "CC", "kind": "analyze"}]


@pytest.mark.asyncio
async def test_switchable_delegates_fix(two_providers):
    sw = SwitchableAIProvider(two_providers, active="opencode")
    chunks = [c.text async for c in sw.stream_fix("/tmp/x", "o/r", 1, "body")]
    assert chunks == ["OC:fix"]


@pytest.mark.asyncio
async def test_switchable_delegates_generate_text(two_providers):
    sw = SwitchableAIProvider(two_providers, active="claude-code")
    assert await sw.generate_text("hi") == "CC:text"


# --- HTTP endpoints ---


@pytest.fixture
def client():
    return TestClient(app)


def test_get_providers_returns_status(client):
    res = client.get("/api/providers")
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) >= {"active", "from_env", "supported", "available", "clis"}
    assert body["active"] in body["supported"]
    assert set(body["supported"]) == {"opencode", "claude-code"}
    assert set(body["available"].keys()) == {"opencode", "claude-code"}
    assert body["clis"] == {"opencode": "opencode", "claude-code": "claude"}


def test_get_config_includes_provider_status(client):
    res = client.get("/api/config")
    assert res.status_code == 200
    body = res.json()
    # Backwards-compat fields preserved.
    assert body["ai_provider"] in {"opencode", "claude-code"}
    assert body["supported_providers"] == ["opencode", "claude-code"]
    # New nested status block.
    assert body["providers"]["active"] == body["ai_provider"]
    assert "available" in body["providers"]


def test_set_provider_switches_active(client):
    # Switch to claude-code.
    res = client.post("/api/provider", json={"name": "claude-code"})
    assert res.status_code == 200
    assert res.json()["active"] == "claude-code"

    # Confirm via /api/config.
    assert client.get("/api/config").json()["ai_provider"] == "claude-code"

    # Switch back.
    res = client.post("/api/provider", json={"name": "opencode"})
    assert res.status_code == 200
    assert res.json()["active"] == "opencode"


def test_set_provider_rejects_unknown(client):
    res = client.post("/api/provider", json={"name": "no-such-thing"})
    assert res.status_code == 400
    assert "supported" in res.json()["detail"].lower()


def test_set_provider_warns_if_cli_missing(client):
    """When the picked provider's CLI is missing, the response body carries a warning."""
    with patch("app.main._provider_available", return_value=False):
        res = client.post("/api/provider", json={"name": "claude-code"})
    assert res.status_code == 200
    body = res.json()
    assert body["active"] == "claude-code"
    assert body["warning"] is not None
    assert "claude" in body["warning"]
    # Restore active to opencode for subsequent tests.
    client.post("/api/provider", json={"name": "opencode"})


def test_models_returns_empty_with_warning_when_cli_missing(client):
    """The /api/models endpoint must not 500 when the active provider's CLI isn't installed."""
    # Force opencode active and pretend its CLI is missing.
    client.post("/api/provider", json={"name": "opencode"})
    with patch("app.main._provider_available", return_value=False):
        res = client.get("/api/models")
    assert res.status_code == 200
    body = res.json()
    assert body["models"] == []
    assert "warning" in body


# --- Claude model listing ---


def test_claude_code_supported_without_env_flag(monkeypatch):
    """claude-code is a first-class provider: registered even when the legacy
    ENABLE_CLAUDE_CODE env var is absent."""
    import importlib

    import app.main as main

    monkeypatch.delenv("ENABLE_CLAUDE_CODE", raising=False)
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    main = importlib.reload(main)
    try:
        assert "claude-code" in main._SUPPORTED_PROVIDERS
        assert main._PROVIDER_CLIS["claude-code"] == "claude"
    finally:
        importlib.reload(main)


def test_models_endpoint_returns_universal_aliases_for_claude_code(client):
    """Switching to claude-code exposes the universal model aliases."""
    client.post("/api/provider", json={"name": "claude-code"})
    res = client.get("/api/models")
    assert res.status_code == 200
    assert res.json()["models"] == ["fable", "opus", "sonnet", "haiku"]
    # Restore.
    client.post("/api/provider", json={"name": "opencode"})


def test_models_endpoint_returns_claude_models(client, monkeypatch):
    """GET /api/models for claude-code delegates to the adapter's list_models()."""
    import app.adapters.ai.claude_code_adapter as _cc

    monkeypatch.setattr(_cc, "list_models", lambda: ["opus", "sonnet", "haiku", "claude-sonnet-4-6"])
    client.post("/api/provider", json={"name": "claude-code"})
    res = client.get("/api/models")
    assert res.status_code == 200
    body = res.json()
    assert body["models"] == ["opus", "sonnet", "haiku", "claude-sonnet-4-6"]
    # Restore.
    client.post("/api/provider", json={"name": "opencode"})
