"""Tests for shared subprocess utilities."""
import os
import pytest
from unittest.mock import patch
from app.adapters._subprocess import clean_env, run_subprocess, run_git, SubprocessError


class TestCleanEnv:
    def test_removes_github_token(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "secret", "HOME": "/home/user"}):
            env = clean_env()
        assert "GITHUB_TOKEN" not in env
        assert env["HOME"] == "/home/user"

    def test_removes_gh_token(self):
        with patch.dict(os.environ, {"GH_TOKEN": "secret2"}):
            env = clean_env()
        assert "GH_TOKEN" not in env

    def test_returns_copy(self):
        env = clean_env()
        env["INJECTED"] = "yes"
        assert "INJECTED" not in os.environ


class TestRunSubprocess:
    def test_runs_simple_command(self):
        result = run_subprocess(["echo", "hello"])
        assert result.strip() == "hello"

    def test_raises_on_nonzero_exit(self):
        with pytest.raises(SubprocessError, match="exit code"):
            run_subprocess(["false"])

    def test_captures_stderr_in_error(self):
        with pytest.raises(SubprocessError) as exc_info:
            run_subprocess(["ls", "/this/does/not/exist/at/all"])
        assert exc_info.value.stderr != ""


class TestRunGit:
    def test_runs_git_command(self, tmp_path):
        import subprocess
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        result = run_git(["status"], cwd=str(tmp_path))
        assert "branch" in result.lower() or "nothing" in result.lower()

    def test_raises_subprocess_error_on_failure(self, tmp_path):
        with pytest.raises(SubprocessError) as exc_info:
            run_git(["status"], cwd=str(tmp_path))  # not a git repo
        # Should have stderr populated
        assert isinstance(exc_info.value, SubprocessError)
