"""Tests for the AI provider factory in app.main."""
import pytest

from app.adapters.ai.claude_code_adapter import ClaudeCodeAdapter
from app.adapters.ai.opencode_adapter import OpenCodeAdapter
from app.main import _build_ai_provider


def test_build_ai_provider_opencode():
    assert isinstance(_build_ai_provider("opencode"), OpenCodeAdapter)


def test_build_ai_provider_claude_code():
    assert isinstance(_build_ai_provider("claude-code"), ClaudeCodeAdapter)


def test_build_ai_provider_unknown_raises():
    with pytest.raises(ValueError, match="Unknown AI_PROVIDER"):
        _build_ai_provider("not-a-real-provider")
