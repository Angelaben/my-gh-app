"""Tests for settings_store: runtime review settings + custom prompts."""
import pytest

from app.services import settings_store as ss


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(ss, "_SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(ss, "_PROMPTS_PATH", tmp_path / "prompts.json")


class TestReviewSettings:
    def test_defaults_when_unset(self, monkeypatch):
        for env in (
            "REVIEW_TIMEOUT", "REVIEW_DIFF_MAX_CHARS",
            "REVIEW_MAX_CONCURRENCY", "REVIEW_IGNORE_GLOBS",
        ):
            monkeypatch.delenv(env, raising=False)
        s = ss.get_review_settings()
        assert s["review_timeout"] == ss.DEFAULT_REVIEW_TIMEOUT
        assert s["review_diff_max_chars"] == ss.DEFAULT_DIFF_MAX_CHARS
        assert s["review_max_concurrency"] == ss.DEFAULT_MAX_CONCURRENCY
        assert isinstance(s["review_ignore_globs"], list) and s["review_ignore_globs"]

    def test_save_and_read_back(self):
        ss.save_review_settings({"review_timeout": 120, "review_diff_max_chars": 5000})
        s = ss.get_review_settings()
        assert s["review_timeout"] == 120
        assert s["review_diff_max_chars"] == 5000

    def test_save_validates_bounds(self):
        with pytest.raises(ValueError):
            ss.save_review_settings({"review_timeout": 5})  # below 30
        with pytest.raises(ValueError):
            ss.save_review_settings({"review_max_concurrency": 999})  # above 16

    def test_save_ignore_globs_accepts_string_and_list(self):
        ss.save_review_settings({"review_ignore_globs": "*.lock, dist/*"})
        assert ss.get_review_settings()["review_ignore_globs"] == ["*.lock", "dist/*"]
        ss.save_review_settings({"review_ignore_globs": ["a", "b"]})
        assert ss.get_review_settings()["review_ignore_globs"] == ["a", "b"]

    def test_reset(self, monkeypatch):
        monkeypatch.delenv("REVIEW_TIMEOUT", raising=False)
        ss.save_review_settings({"review_timeout": 120})
        assert ss.get_review_settings()["review_timeout"] == 120
        ss.reset_review_settings()
        assert ss.get_review_settings()["review_timeout"] == ss.DEFAULT_REVIEW_TIMEOUT

    def test_stored_overrides_env(self, monkeypatch):
        monkeypatch.setenv("REVIEW_DIFF_MAX_CHARS", "9999")
        ss.save_review_settings({"review_diff_max_chars": 4000})
        assert ss.get_review_settings()["review_diff_max_chars"] == 4000


class TestPrompts:
    def test_defaults_are_not_custom(self):
        prompts = ss.get_prompts()
        assert set(prompts) == {"review", "fix", "analyze"}
        assert prompts["review"]["is_custom"] is False
        assert prompts["review"]["current"] == ss.DEFAULT_PROMPTS["review"]

    def test_save_and_get_prompt(self):
        body = "Review PR #{pr_number} in {repo_full_name}. Be terse."
        ss.save_prompt("review", body)
        assert ss.get_prompt("review") == body
        assert ss.get_prompts()["review"]["is_custom"] is True

    def test_save_rejects_missing_placeholder(self):
        with pytest.raises(ValueError, match="Missing required placeholder"):
            ss.save_prompt("review", "Review the diff please.")

    def test_save_rejects_unknown_placeholder(self):
        with pytest.raises(ValueError, match="Unknown placeholder"):
            ss.save_prompt("review", "PR #{pr_number} in {repo_full_name} {bogus}")

    def test_save_rejects_empty(self):
        with pytest.raises(ValueError):
            ss.save_prompt("review", "   ")

    def test_reset_prompt_restores_default(self):
        ss.save_prompt("review", "PR #{pr_number} {repo_full_name}")
        ss.reset_prompt("review")
        assert ss.get_prompt("review") == ss.DEFAULT_PROMPTS["review"]
        assert ss.get_prompts()["review"]["is_custom"] is False

    def test_unknown_prompt_name_raises(self):
        with pytest.raises(KeyError):
            ss.save_prompt("bogus", "x")
        with pytest.raises(KeyError):
            ss.get_prompt("bogus")

    def test_fix_prompt_requires_comment_body(self):
        with pytest.raises(ValueError):
            ss.save_prompt("fix", "Fix PR #{pr_number} in {repo_full_name}")
        ss.save_prompt("fix", "Fix PR #{pr_number} in {repo_full_name}: {comment_body}")
        assert "{comment_body}" in ss.get_prompt("fix")

    def test_shipped_defaults_pass_validation(self):
        # The defaults must themselves be valid (doubled braces, right placeholders).
        for name in ss.DEFAULT_PROMPTS:
            ss._validate_prompt(name, ss.DEFAULT_PROMPTS[name])
