# Backend Hexagonal Architecture Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the backend from a monolithic `main.py` + `gh.py` + `opencode.py` + `cache.py` into a hexagonal (ports & adapters) architecture that makes swapping AI providers (OpenCode → Bedrock, Claude Code) trivial.

**Architecture:** Domain models live in `app/domain/`, port ABCs in `app/ports/`, concrete adapters in `app/adapters/`, orchestration services in `app/services/`, and `app/main.py` becomes thin FastAPI controllers wired by dependency injection. The central abstraction is `AIProvider` — all AI calls go through this port, so adding a new provider means adding one file in `app/adapters/ai/`.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, dataclasses, abc.ABC, asyncio, pytest + pytest-asyncio

---

## File Structure

```
app/
  domain/
    __init__.py
    models.py          # Pure dataclasses: Review, Finding, Repo, PR, Comment, FixResult
    exceptions.py      # Domain exceptions: ProviderError, VCSError, CacheError, WorktreeError
  ports/
    __init__.py
    ai_provider.py     # AIProvider ABC: stream_review, run_review, analyze_comments, stream_fix, generate_text
    vcs_port.py        # VCSPort ABC: list_prs, get_diff, get_comments, post_comment, post_inline_comment
    worktree_port.py   # WorktreePort ABC: create, remove, list, has_changes, get_diff, stage_all, commit_and_push, create_branch_and_push
    cache_port.py      # CachePort ABC: get_repos, add_repo, remove_repo, get_prs, save_prs, get_review, save_review, clear_review
  adapters/
    __init__.py
    _subprocess.py     # Shared: clean_env(), run_subprocess(), run_git()
    ai/
      __init__.py
      opencode_adapter.py   # OpenCode implementation of AIProvider
    vcs/
      __init__.py
      github_cli_adapter.py # gh CLI implementation of VCSPort
    worktree/
      __init__.py
      git_worktree_adapter.py  # git worktree implementation of WorktreePort
    cache/
      __init__.py
      json_file_cache.py       # JSON files implementation of CachePort
  services/
    __init__.py
    review_service.py    # ReviewService: run_review, stream_review, rerun_review
    fix_service.py       # FixService: stream_fix, push_fix, create_pr_from_fix
    comment_service.py   # CommentService: get_comments, analyze_comments
  main.py                # Thin FastAPI controllers + DI wiring only
tests/
  __init__.py
  unit/
    __init__.py
    test_domain.py
    test_review_service.py
    test_fix_service.py
    test_comment_service.py
    test_json_cache.py
  conftest.py
```

**Files deleted after Task 11:** `app/gh.py`, `app/opencode.py`, `app/cache.py`

---

## Task 1: Domain Models and Exceptions

**Files:**
- Create: `app/domain/__init__.py`
- Create: `app/domain/models.py`
- Create: `app/domain/exceptions.py`
- Create: `tests/__init__.py`
- Create: `tests/unit/__init__.py`
- Create: `tests/unit/test_domain.py`
- Create: `tests/conftest.py`
- Modify: `pyproject.toml` (add pytest dependencies)

- [ ] **Step 1: Add pytest to pyproject.toml**

Edit `pyproject.toml` to:
```toml
[project]
name = "gh-review-tool"
requires-python = ">=3.12"
dependencies = ["fastapi>=0.115", "uvicorn>=0.34"]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

Then run:
```bash
uv pip install -e ".[dev]"
```

- [ ] **Step 2: Write failing tests for domain models**

Create `tests/__init__.py` (empty), `tests/unit/__init__.py` (empty), and `tests/conftest.py`:
```python
# tests/conftest.py
# Shared fixtures will go here in later tasks.
```

Create `tests/unit/test_domain.py`:
```python
"""Tests for domain models and exceptions."""
import pytest
from app.domain.models import Finding, Review, Repo, PR, Comment, FixResult
from app.domain.exceptions import ProviderError, VCSError, CacheError, WorktreeError


class TestFinding:
    def test_finding_creation(self):
        f = Finding(
            priority="P1",
            title="Null check missing",
            description="Field may be None",
            file="app/main.py",
            line="42",
            suggestion="Add None guard",
        )
        assert f.priority == "P1"
        assert f.file == "app/main.py"

    def test_finding_optional_fields(self):
        f = Finding(priority="P2", title="Style issue", description="Use f-string")
        assert f.file is None
        assert f.line is None
        assert f.suggestion is None


class TestReview:
    def test_review_creation(self):
        r = Review(summary="LGTM", findings=[])
        assert r.summary == "LGTM"
        assert r.findings == []

    def test_review_with_findings(self):
        f = Finding(priority="P0", title="SQL injection", description="Unescaped input")
        r = Review(summary="Critical issues found", findings=[f])
        assert len(r.findings) == 1
        assert r.findings[0].priority == "P0"

    def test_review_raw_output_optional(self):
        r = Review(summary="Unparseable", findings=[], raw_output="raw text", raw_length=8)
        assert r.raw_output == "raw text"


class TestRepo:
    def test_repo_full_name(self):
        repo = Repo(owner="acme", name="backend")
        assert repo.full_name == "acme/backend"


class TestComment:
    def test_comment_defaults(self):
        c = Comment(id=1, author="alice", body="LGTM")
        assert c.file is None
        assert c.line is None
        assert c.created_at is None


class TestFixResult:
    def test_fix_result(self):
        fr = FixResult(
            worktree_path="/tmp/wt",
            branch="feature/fix",
            has_changes=True,
            diff="--- a/foo.py\n+++ b/foo.py",
            output="Done",
        )
        assert fr.has_changes is True


class TestExceptions:
    def test_provider_error(self):
        e = ProviderError("opencode timed out")
        assert "opencode timed out" in str(e)

    def test_vcs_error(self):
        e = VCSError("gh command failed")
        assert isinstance(e, VCSError)

    def test_cache_error(self):
        e = CacheError("JSON decode error")
        assert isinstance(e, CacheError)

    def test_worktree_error(self):
        e = WorktreeError("branch not found")
        assert isinstance(e, WorktreeError)
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /Users/angelardbenjamin/working_directory/gh-review-tool
python -m pytest tests/unit/test_domain.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.domain'`

- [ ] **Step 4: Create domain package**

Create `app/domain/__init__.py`:
```python
"""Domain layer — pure Python, no framework dependencies."""
```

Create `app/domain/models.py`:
```python
"""Pure domain models. No framework dependencies."""
from dataclasses import dataclass, field


@dataclass
class Finding:
    priority: str  # P0 | P1 | P2 | P3
    title: str
    description: str
    file: str | None = None
    line: str | None = None
    suggestion: str | None = None


@dataclass
class Review:
    summary: str
    findings: list[Finding]
    raw_output: str | None = None
    raw_length: int | None = None


@dataclass
class Repo:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass
class PR:
    number: int
    title: str
    author: str
    branch: str
    base_branch: str
    additions: int
    deletions: int
    updated_at: str
    url: str


@dataclass
class Comment:
    id: int
    author: str
    body: str
    file: str | None = None
    line: int | None = None
    created_at: str | None = None


@dataclass
class FixResult:
    worktree_path: str
    branch: str
    has_changes: bool
    diff: str
    output: str
```

Create `app/domain/exceptions.py`:
```python
"""Domain exceptions — one per failure category."""


class ProviderError(Exception):
    """AI provider call failed (timeout, parse error, subprocess failure)."""


class VCSError(Exception):
    """VCS operation failed (gh CLI, git, GitHub API)."""


class CacheError(Exception):
    """Cache read/write failed (JSON decode, file permission)."""


class WorktreeError(Exception):
    """Worktree operation failed (clone, checkout, push)."""
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/unit/test_domain.py -v
```

Expected: all 14 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add app/domain/ tests/ pyproject.toml
git commit -m "feat: add domain models, exceptions, and test scaffold"
```

---

## Task 2: Port Interfaces (ABCs)

**Files:**
- Create: `app/ports/__init__.py`
- Create: `app/ports/ai_provider.py`
- Create: `app/ports/vcs_port.py`
- Create: `app/ports/worktree_port.py`
- Create: `app/ports/cache_port.py`

- [ ] **Step 1: Write failing import test**

Add to `tests/unit/test_domain.py` (append at end of file):
```python
class TestPortImports:
    def test_ai_provider_importable(self):
        from app.ports.ai_provider import AIProvider  # noqa: F401

    def test_vcs_port_importable(self):
        from app.ports.vcs_port import VCSPort  # noqa: F401

    def test_worktree_port_importable(self):
        from app.ports.worktree_port import WorktreePort  # noqa: F401

    def test_cache_port_importable(self):
        from app.ports.cache_port import CachePort  # noqa: F401
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/unit/test_domain.py::TestPortImports -v
```

Expected: `ModuleNotFoundError: No module named 'app.ports'`

- [ ] **Step 3: Create ports package**

Create `app/ports/__init__.py`:
```python
"""Port interfaces — ABCs defining what adapters must implement."""
```

Create `app/ports/ai_provider.py`:
```python
"""AI provider port — swap opencode / Bedrock / Claude Code behind this interface."""
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import TypeAlias

from app.domain.models import Comment, Review


class ReviewChunkEvent:
    """Raw text chunk streamed from the AI provider during review."""
    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text


class ReviewResultEvent:
    """Structured review result emitted once the provider finishes."""
    __slots__ = ("review",)

    def __init__(self, review: Review) -> None:
        self.review = review


ReviewStreamEvent: TypeAlias = ReviewChunkEvent | ReviewResultEvent


class FixChunkEvent:
    """Raw text chunk streamed from the AI provider during fix generation."""
    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text


class AIProvider(ABC):
    """Abstract AI provider. Implement this to add a new provider."""

    @abstractmethod
    async def stream_review(
        self, repo_full_name: str, pr_number: int, diff: str
    ) -> AsyncGenerator[ReviewStreamEvent, None]:
        """Stream review output. Must yield ReviewChunkEvent(s) then a final ReviewResultEvent."""
        ...

    @abstractmethod
    async def analyze_comments(
        self, repo_full_name: str, pr_number: int, comments: list[Comment]
    ) -> list[dict]:
        """Analyze comments and return analysis dicts."""
        ...

    @abstractmethod
    async def stream_fix(
        self, repo_dir: str, repo_full_name: str, pr_number: int, comment_body: str
    ) -> AsyncGenerator[FixChunkEvent, None]:
        """Stream fix implementation output."""
        ...

    @abstractmethod
    async def generate_text(self, prompt: str, timeout: int = 60) -> str:
        """Generate free-form text (used for commit messages, PR descriptions)."""
        ...
```

Create `app/ports/vcs_port.py`:
```python
"""VCS port — GitHub operations (PRs, diffs, comments)."""
from abc import ABC, abstractmethod

from app.domain.models import PR, Comment


class VCSPort(ABC):
    """Abstract VCS provider. Implement this to add a new VCS backend."""

    @abstractmethod
    def list_prs(self, repo_full_name: str) -> list[PR]:
        """List open pull requests for a repository."""
        ...

    @abstractmethod
    def get_diff(self, repo_full_name: str, pr_number: int) -> str:
        """Return the unified diff for a PR."""
        ...

    @abstractmethod
    def get_comments(self, repo_full_name: str, pr_number: int) -> dict:
        """Return comments, reviews, and inline review comments for a PR."""
        ...

    @abstractmethod
    def post_comment(self, repo_full_name: str, pr_number: int, body: str) -> str:
        """Post a top-level comment on a PR."""
        ...

    @abstractmethod
    def post_inline_comment(
        self,
        repo_full_name: str,
        pr_number: int,
        body: str,
        path: str,
        line: int,
        commit_id: str | None = None,
    ) -> dict:
        """Post an inline review comment on a specific file/line."""
        ...

    @abstractmethod
    def get_pr_head_branch(self, repo_full_name: str, pr_number: int) -> str:
        """Return the head branch name for a PR."""
        ...

    @abstractmethod
    def search_repos(self, org: str, query: str = "") -> list[dict]:
        """Search repositories in an organization."""
        ...
```

Create `app/ports/worktree_port.py`:
```python
"""Worktree port — local git checkout management."""
from abc import ABC, abstractmethod


class WorktreePort(ABC):
    """Abstract worktree manager."""

    @abstractmethod
    def create(self, repo_full_name: str, pr_number: int, pr_branch: str) -> str:
        """Create (or reset) a worktree for the PR branch. Returns the path."""
        ...

    @abstractmethod
    def remove(self, repo_full_name: str, pr_number: int) -> None:
        """Remove a worktree and prune stale references."""
        ...

    @abstractmethod
    def list_worktrees(self) -> list[dict]:
        """List all existing worktrees as {name, path} dicts."""
        ...

    @abstractmethod
    def has_changes(self, worktree_path: str) -> bool:
        """Return True if the worktree has uncommitted changes."""
        ...

    @abstractmethod
    def stage_all(self, worktree_path: str) -> None:
        """Stage all changes (git add -A) without side effects in unrelated calls."""
        ...

    @abstractmethod
    def get_staged_diff(self, worktree_path: str) -> str:
        """Return the diff of staged changes (must call stage_all first)."""
        ...

    @abstractmethod
    def commit_and_push(self, worktree_path: str, message: str) -> str:
        """Commit staged changes and push to origin. Returns push output."""
        ...

    @abstractmethod
    def create_branch_and_push(
        self, worktree_path: str, new_branch: str, message: str
    ) -> str:
        """Create a new branch, commit all changes, and push."""
        ...

    @abstractmethod
    def worktree_path(self, repo_full_name: str, pr_number: int) -> str:
        """Return the expected filesystem path for a worktree (may not exist yet)."""
        ...
```

Create `app/ports/cache_port.py`:
```python
"""Cache port — persisting repos, PRs, and reviews."""
from abc import ABC, abstractmethod

from app.domain.models import PR, Review


class CachePort(ABC):
    """Abstract cache. Implement this to add a new storage backend."""

    @abstractmethod
    def get_repos(self) -> list[dict]:
        """Return all tracked repos."""
        ...

    @abstractmethod
    def add_repo(self, owner: str, name: str) -> list[dict]:
        """Add a repo and return the updated list."""
        ...

    @abstractmethod
    def remove_repo(self, full_name: str) -> list[dict]:
        """Remove a repo and return the updated list."""
        ...

    @abstractmethod
    def get_prs(self, repo_full_name: str) -> list[dict]:
        """Return cached PRs for a repo."""
        ...

    @abstractmethod
    def save_prs(self, repo_full_name: str, prs: list[dict]) -> None:
        """Persist PRs for a repo."""
        ...

    @abstractmethod
    def get_review(self, repo_full_name: str, pr_number: int) -> Review | None:
        """Return cached review for a PR, or None."""
        ...

    @abstractmethod
    def save_review(self, repo_full_name: str, pr_number: int, review: Review) -> None:
        """Persist a review for a PR."""
        ...

    @abstractmethod
    def clear_review(self, repo_full_name: str, pr_number: int) -> None:
        """Remove cached review for a PR."""
        ...
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/unit/test_domain.py::TestPortImports -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/ports/
git commit -m "feat: add port ABCs for AI, VCS, worktree, and cache"
```

---

## Task 3: Shared Subprocess Utilities

**Files:**
- Create: `app/adapters/__init__.py`
- Create: `app/adapters/_subprocess.py`

- [ ] **Step 1: Write failing test**

Create `tests/unit/test_subprocess_utils.py`:
```python
"""Tests for shared subprocess utilities."""
import os
import pytest
from unittest.mock import patch
from app.adapters._subprocess import clean_env, run_subprocess, SubprocessError


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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/unit/test_subprocess_utils.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.adapters'`

- [ ] **Step 3: Implement**

Create `app/adapters/__init__.py`:
```python
"""Adapter implementations — one per external system."""
```

Create `app/adapters/_subprocess.py`:
```python
"""Shared subprocess helpers used by all adapters."""
import os
import subprocess


class SubprocessError(Exception):
    """A subprocess returned a non-zero exit code."""

    def __init__(self, message: str, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


def clean_env() -> dict[str, str]:
    """Return a copy of the environment without GitHub tokens.

    Removes GITHUB_TOKEN and GH_TOKEN so gh CLI uses its keyring auth,
    and opencode sub-processes don't inherit the caller's token.
    """
    env = os.environ.copy()
    env.pop("GITHUB_TOKEN", None)
    env.pop("GH_TOKEN", None)
    return env


def run_subprocess(
    args: list[str],
    *,
    cwd: str | None = None,
    timeout: int = 60,
    input: str | None = None,  # noqa: A002
) -> str:
    """Run a subprocess and return stdout. Raises SubprocessError on failure."""
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=clean_env(),
        cwd=cwd,
        input=input,
    )
    if result.returncode != 0:
        raise SubprocessError(
            f"{args[0]!r} exit code {result.returncode}: {result.stderr.strip()}",
            stderr=result.stderr.strip(),
        )
    return result.stdout.strip()


def run_git(args: list[str], *, cwd: str, timeout: int = 120) -> str:
    """Run a git command in a specific directory."""
    try:
        return run_subprocess(["git", *args], cwd=cwd, timeout=timeout)
    except SubprocessError as e:
        raise SubprocessError(f"git {args[0]}: {e}", stderr=e.stderr) from e
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/unit/test_subprocess_utils.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/adapters/__init__.py app/adapters/_subprocess.py tests/unit/test_subprocess_utils.py
git commit -m "feat: add shared subprocess utilities (clean_env, run_subprocess, run_git)"
```

---

## Task 4: JSON File Cache Adapter

**Files:**
- Create: `app/adapters/cache/__init__.py`
- Create: `app/adapters/cache/json_file_cache.py`
- Create: `tests/unit/test_json_cache.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_json_cache.py`:
```python
"""Tests for the JSON file cache adapter."""
import pytest
from pathlib import Path
from app.adapters.cache.json_file_cache import JsonFileCache
from app.domain.models import Finding, Review


@pytest.fixture
def cache(tmp_path: Path) -> JsonFileCache:
    return JsonFileCache(cache_dir=tmp_path)


class TestRepos:
    def test_empty_by_default(self, cache: JsonFileCache):
        assert cache.get_repos() == []

    def test_add_repo(self, cache: JsonFileCache):
        result = cache.add_repo("acme", "backend")
        assert len(result) == 1
        assert result[0]["full_name"] == "acme/backend"
        assert result[0]["owner"] == "acme"
        assert result[0]["name"] == "backend"

    def test_add_repo_idempotent(self, cache: JsonFileCache):
        cache.add_repo("acme", "backend")
        result = cache.add_repo("acme", "backend")
        assert len(result) == 1

    def test_remove_repo(self, cache: JsonFileCache):
        cache.add_repo("acme", "backend")
        result = cache.remove_repo("acme/backend")
        assert result == []

    def test_remove_repo_cleans_pr_cache(self, cache: JsonFileCache):
        cache.add_repo("acme", "backend")
        cache.save_prs("acme/backend", [{"number": 1}])
        cache.remove_repo("acme/backend")
        assert cache.get_prs("acme/backend") == []


class TestPRs:
    def test_empty_by_default(self, cache: JsonFileCache):
        assert cache.get_prs("acme/backend") == []

    def test_save_and_retrieve(self, cache: JsonFileCache):
        prs = [{"number": 1, "title": "fix bug"}]
        cache.save_prs("acme/backend", prs)
        assert cache.get_prs("acme/backend") == prs


class TestReview:
    def test_none_when_missing(self, cache: JsonFileCache):
        assert cache.get_review("acme/backend", 42) is None

    def test_save_and_retrieve(self, cache: JsonFileCache):
        review = Review(
            summary="LGTM",
            findings=[Finding(priority="P2", title="Style", description="Use f-string")],
        )
        cache.save_review("acme/backend", 42, review)
        loaded = cache.get_review("acme/backend", 42)
        assert loaded is not None
        assert loaded.summary == "LGTM"
        assert len(loaded.findings) == 1
        assert loaded.findings[0].priority == "P2"

    def test_clear_review(self, cache: JsonFileCache):
        review = Review(summary="ok", findings=[])
        cache.save_review("acme/backend", 1, review)
        cache.clear_review("acme/backend", 1)
        assert cache.get_review("acme/backend", 1) is None

    def test_clear_nonexistent_is_noop(self, cache: JsonFileCache):
        cache.clear_review("acme/backend", 999)  # Should not raise
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/unit/test_json_cache.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.adapters.cache'`

- [ ] **Step 3: Implement**

Create `app/adapters/cache/__init__.py`:
```python
"""JSON file cache adapter."""
```

Create `app/adapters/cache/json_file_cache.py`:
```python
"""JSON-on-disk implementation of CachePort."""
import json
from pathlib import Path

from app.domain.exceptions import CacheError
from app.domain.models import Finding, Review
from app.ports.cache_port import CachePort


def _slug(repo_full_name: str) -> str:
    return repo_full_name.replace("/", "_")


class JsonFileCache(CachePort):
    """Stores repos, PRs, and reviews as JSON files under cache_dir."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        if cache_dir is None:
            cache_dir = Path(__file__).parent.parent.parent.parent / ".cache"
        self._dir = cache_dir
        self._prs_dir = self._dir / "prs"
        self._reviews_dir = self._dir / "reviews"

    def _ensure_dirs(self) -> None:
        self._prs_dir.mkdir(parents=True, exist_ok=True)
        self._reviews_dir.mkdir(parents=True, exist_ok=True)

    def _read_json(self, path: Path) -> dict | list | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError as e:
            raise CacheError(f"Failed to parse {path}: {e}") from e

    def _write_json(self, path: Path, data: dict | list) -> None:
        self._ensure_dirs()
        path.write_text(json.dumps(data, indent=2))

    # --- Repos ---

    def get_repos(self) -> list[dict]:
        return self._read_json(self._dir / "repos.json") or []

    def add_repo(self, owner: str, name: str) -> list[dict]:
        repos = self.get_repos()
        entry = {"owner": owner, "name": name, "full_name": f"{owner}/{name}"}
        if not any(r["full_name"] == entry["full_name"] for r in repos):
            repos.append(entry)
            self._write_json(self._dir / "repos.json", repos)
        return repos

    def remove_repo(self, full_name: str) -> list[dict]:
        repos = [r for r in self.get_repos() if r["full_name"] != full_name]
        self._write_json(self._dir / "repos.json", repos)
        pr_file = self._prs_dir / f"{_slug(full_name)}.json"
        if pr_file.exists():
            pr_file.unlink()
        return repos

    # --- PRs ---

    def get_prs(self, repo_full_name: str) -> list[dict]:
        path = self._prs_dir / f"{_slug(repo_full_name)}.json"
        return self._read_json(path) or []

    def save_prs(self, repo_full_name: str, prs: list[dict]) -> None:
        self._write_json(self._prs_dir / f"{_slug(repo_full_name)}.json", prs)

    # --- Reviews ---

    def _review_path(self, repo_full_name: str, pr_number: int) -> Path:
        return self._reviews_dir / f"{_slug(repo_full_name)}_{pr_number}.json"

    def get_review(self, repo_full_name: str, pr_number: int) -> Review | None:
        data = self._read_json(self._review_path(repo_full_name, pr_number))
        if data is None:
            return None
        findings = [
            Finding(
                priority=f.get("priority", f.get("criticality", "P3")),
                title=f.get("title", ""),
                description=f.get("description", ""),
                file=f.get("file"),
                line=f.get("line"),
                suggestion=f.get("suggestion"),
            )
            for f in data.get("findings", [])
        ]
        return Review(
            summary=data.get("summary", ""),
            findings=findings,
            raw_output=data.get("raw_output"),
            raw_length=data.get("raw_length"),
        )

    def save_review(self, repo_full_name: str, pr_number: int, review: Review) -> None:
        data = {
            "summary": review.summary,
            "findings": [
                {
                    "priority": f.priority,
                    "title": f.title,
                    "description": f.description,
                    "file": f.file,
                    "line": f.line,
                    "suggestion": f.suggestion,
                }
                for f in review.findings
            ],
        }
        if review.raw_output is not None:
            data["raw_output"] = review.raw_output
        if review.raw_length is not None:
            data["raw_length"] = review.raw_length
        self._write_json(self._review_path(repo_full_name, pr_number), data)

    def clear_review(self, repo_full_name: str, pr_number: int) -> None:
        path = self._review_path(repo_full_name, pr_number)
        if path.exists():
            path.unlink()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/unit/test_json_cache.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/adapters/cache/ tests/unit/test_json_cache.py
git commit -m "feat: add JSON file cache adapter with full test coverage"
```

---

## Task 5: GitHub CLI VCS Adapter

**Files:**
- Create: `app/adapters/vcs/__init__.py`
- Create: `app/adapters/vcs/github_cli_adapter.py`

- [ ] **Step 1: Write failing import test**

Add to `tests/unit/test_domain.py` (append to TestPortImports class):
```python
    def test_github_cli_adapter_importable(self):
        from app.adapters.vcs.github_cli_adapter import GitHubCLIAdapter  # noqa: F401
```

Run:
```bash
python -m pytest tests/unit/test_domain.py::TestPortImports::test_github_cli_adapter_importable -v
```

Expected: `ModuleNotFoundError: No module named 'app.adapters.vcs'`

- [ ] **Step 2: Implement**

Create `app/adapters/vcs/__init__.py`:
```python
"""VCS adapters."""
```

Create `app/adapters/vcs/github_cli_adapter.py`:
```python
"""GitHub CLI implementation of VCSPort."""
import json
import subprocess

from app.adapters._subprocess import SubprocessError, clean_env, run_subprocess
from app.domain.exceptions import VCSError
from app.domain.models import Comment, PR
from app.ports.vcs_port import VCSPort


class GitHubCLIAdapter(VCSPort):
    """Implements VCSPort using the `gh` CLI tool."""

    def _run(self, args: list[str], *, cwd: str | None = None, timeout: int = 60) -> str:
        try:
            return run_subprocess(["gh", *args], cwd=cwd, timeout=timeout)
        except SubprocessError as e:
            raise VCSError(f"gh command failed: {e}") from e

    def search_repos(self, org: str, query: str = "") -> list[dict]:
        args = ["repo", "list", org, "--json", "name,owner,description,url", "--limit", "100"]
        raw = self._run(args)
        repos = json.loads(raw) if raw else []
        if query:
            q = query.lower()
            repos = [
                r for r in repos
                if q in r.get("name", "").lower() or q in (r.get("description") or "").lower()
            ]
        return repos

    def list_prs(self, repo_full_name: str) -> list[PR]:
        raw = self._run([
            "pr", "list",
            "--repo", repo_full_name,
            "--json", "number,title,author,url,updatedAt,headRefName,baseRefName,state,additions,deletions",
            "--limit", "50",
            "--state", "open",
        ])
        prs = json.loads(raw) if raw else []
        return [
            PR(
                number=pr["number"],
                title=pr["title"],
                author=(
                    pr.get("author", {}).get("login", "unknown")
                    if isinstance(pr.get("author"), dict)
                    else pr.get("author", "unknown")
                ),
                branch=pr["headRefName"],
                base_branch=pr["baseRefName"],
                additions=pr.get("additions", 0),
                deletions=pr.get("deletions", 0),
                updated_at=pr["updatedAt"],
                url=pr["url"],
            )
            for pr in prs
        ]

    def get_diff(self, repo_full_name: str, pr_number: int) -> str:
        return self._run(["pr", "diff", str(pr_number), "--repo", repo_full_name])

    def get_comments(self, repo_full_name: str, pr_number: int) -> dict:
        raw = self._run([
            "pr", "view", str(pr_number),
            "--repo", repo_full_name,
            "--json", "comments,reviews,reviewRequests,body,title,number",
        ])
        data = json.loads(raw) if raw else {}

        try:
            inline_raw = self._run([
                "api", f"repos/{repo_full_name}/pulls/{pr_number}/comments",
                "--paginate",
            ])
            inline_comments = json.loads(inline_raw) if inline_raw else []
            data["review_comments"] = [
                {
                    "author": {"login": c.get("user", {}).get("login", "unknown")},
                    "body": c.get("body", ""),
                    "path": c.get("path", ""),
                    "line": c.get("line"),
                    "created_at": c.get("created_at", ""),
                }
                for c in inline_comments
            ]
        except VCSError:
            data["review_comments"] = []

        return data

    def post_comment(self, repo_full_name: str, pr_number: int, body: str) -> str:
        return self._run([
            "pr", "comment", str(pr_number),
            "--repo", repo_full_name,
            "--body", body,
        ])

    def get_pr_head_sha(self, repo_full_name: str, pr_number: int) -> str:
        raw = self._run([
            "pr", "view", str(pr_number),
            "--repo", repo_full_name,
            "--json", "headRefOid",
        ])
        return json.loads(raw)["headRefOid"]

    def post_inline_comment(
        self,
        repo_full_name: str,
        pr_number: int,
        body: str,
        path: str,
        line: int,
        commit_id: str | None = None,
    ) -> dict:
        if commit_id is None:
            commit_id = self.get_pr_head_sha(repo_full_name, pr_number)

        payload = json.dumps({
            "body": body,
            "commit_id": commit_id,
            "path": path,
            "line": line,
            "side": "RIGHT",
        })

        result = subprocess.run(
            ["gh", "api", f"repos/{repo_full_name}/pulls/{pr_number}/comments",
             "--method", "POST", "--input", "-"],
            input=payload, capture_output=True, text=True, timeout=30, env=clean_env(),
        )
        if result.returncode != 0:
            raise VCSError(f"Failed to post inline comment: {result.stderr.strip()}")
        return json.loads(result.stdout)

    def get_pr_head_branch(self, repo_full_name: str, pr_number: int) -> str:
        raw = self._run([
            "pr", "view", str(pr_number),
            "--repo", repo_full_name,
            "--json", "headRefName",
        ])
        return json.loads(raw)["headRefName"]
```

- [ ] **Step 3: Run import test to verify it passes**

```bash
python -m pytest tests/unit/test_domain.py::TestPortImports::test_github_cli_adapter_importable -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add app/adapters/vcs/
git commit -m "feat: add GitHub CLI VCS adapter"
```

---

## Task 6: Git Worktree Adapter

**Files:**
- Create: `app/adapters/worktree/__init__.py`
- Create: `app/adapters/worktree/git_worktree_adapter.py`

- [ ] **Step 1: Write failing import test**

Add to `tests/unit/test_domain.py` (append to TestPortImports class):
```python
    def test_git_worktree_adapter_importable(self):
        from app.adapters.worktree.git_worktree_adapter import GitWorktreeAdapter  # noqa: F401
```

Run:
```bash
python -m pytest tests/unit/test_domain.py::TestPortImports::test_git_worktree_adapter_importable -v
```

Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 2: Implement**

Create `app/adapters/worktree/__init__.py`:
```python
"""Worktree adapters."""
```

Create `app/adapters/worktree/git_worktree_adapter.py`:
```python
"""Git worktree implementation of WorktreePort."""
import logging
import os
import shutil

from app.adapters._subprocess import SubprocessError, run_git, run_subprocess
from app.domain.exceptions import VCSError, WorktreeError
from app.ports.worktree_port import WorktreePort

logger = logging.getLogger(__name__)

CLONES_BASE = os.path.expanduser("~/.gh-review-tool/clones")
WORKTREES_BASE = os.path.expanduser("~/.gh-review-tool/worktrees")


def _slug(repo_full_name: str) -> str:
    return repo_full_name.replace("/", "_")


class GitWorktreeAdapter(WorktreePort):
    """Uses git worktrees backed by a bare clone per repository."""

    def worktree_path(self, repo_full_name: str, pr_number: int) -> str:
        return os.path.join(WORKTREES_BASE, f"{_slug(repo_full_name)}_pr{pr_number}")

    def _bare_path(self, repo_full_name: str) -> str:
        return os.path.join(CLONES_BASE, f"{_slug(repo_full_name)}.git")

    def _ensure_bare_clone(self, repo_full_name: str) -> str:
        os.makedirs(CLONES_BASE, exist_ok=True)
        bare_path = self._bare_path(repo_full_name)
        if os.path.isdir(bare_path):
            logger.info("Bare clone exists at %s, fetching...", bare_path)
            try:
                run_git(["fetch", "--all"], cwd=bare_path, timeout=120)
            except SubprocessError as e:
                raise WorktreeError(f"Failed to fetch: {e}") from e
            return bare_path
        logger.info("Creating bare clone of %s", repo_full_name)
        try:
            run_subprocess(
                ["gh", "repo", "clone", repo_full_name, bare_path, "--", "--bare"],
                timeout=300,
            )
        except SubprocessError as e:
            raise WorktreeError(f"Failed to clone {repo_full_name}: {e}") from e
        return bare_path

    def create(self, repo_full_name: str, pr_number: int, pr_branch: str) -> str:
        bare_path = self._ensure_bare_clone(repo_full_name)
        os.makedirs(WORKTREES_BASE, exist_ok=True)
        wt_path = self.worktree_path(repo_full_name, pr_number)

        if os.path.isdir(wt_path):
            logger.info("Worktree exists at %s, resetting...", wt_path)
            try:
                run_git(["fetch", "origin", pr_branch], cwd=wt_path, timeout=120)
                run_git(["reset", "--hard", "FETCH_HEAD"], cwd=wt_path)
                run_git(["clean", "-fd"], cwd=wt_path)
            except SubprocessError as e:
                raise WorktreeError(f"Failed to reset worktree: {e}") from e
            return wt_path

        try:
            run_git(["fetch", "origin", f"{pr_branch}:{pr_branch}"], cwd=bare_path, timeout=120)
            run_git(["worktree", "add", wt_path, pr_branch], cwd=bare_path)
        except SubprocessError as e:
            raise WorktreeError(f"Failed to create worktree: {e}") from e
        logger.info("Created worktree at %s on branch %s", wt_path, pr_branch)
        return wt_path

    def remove(self, repo_full_name: str, pr_number: int) -> None:
        bare_path = self._bare_path(repo_full_name)
        wt_path = self.worktree_path(repo_full_name, pr_number)

        if os.path.isdir(wt_path):
            shutil.rmtree(wt_path, ignore_errors=True)

        if os.path.isdir(bare_path):
            try:
                run_git(["worktree", "prune"], cwd=bare_path)
            except SubprocessError:
                pass

        logger.info("Removed worktree for PR #%s", pr_number)

    def list_worktrees(self) -> list[dict]:
        if not os.path.isdir(WORKTREES_BASE):
            return []
        return [
            {"name": entry, "path": os.path.join(WORKTREES_BASE, entry)}
            for entry in os.listdir(WORKTREES_BASE)
            if os.path.isdir(os.path.join(WORKTREES_BASE, entry))
        ]

    def has_changes(self, worktree_path: str) -> bool:
        try:
            result = run_git(["status", "--porcelain"], cwd=worktree_path)
            return bool(result.strip())
        except SubprocessError:
            return False

    def stage_all(self, worktree_path: str) -> None:
        try:
            run_git(["add", "-A"], cwd=worktree_path)
        except SubprocessError as e:
            raise WorktreeError(f"Failed to stage changes: {e}") from e

    def get_staged_diff(self, worktree_path: str) -> str:
        try:
            return run_git(["diff", "--cached"], cwd=worktree_path)
        except SubprocessError as e:
            raise WorktreeError(f"Failed to get diff: {e}") from e

    def commit_and_push(self, worktree_path: str, message: str) -> str:
        try:
            run_git(["add", "-A"], cwd=worktree_path)
            run_git(["commit", "-m", message], cwd=worktree_path, timeout=30)
            return run_git(["push"], cwd=worktree_path, timeout=120)
        except SubprocessError as e:
            raise WorktreeError(f"Failed to commit/push: {e}") from e

    def create_branch_and_push(
        self, worktree_path: str, new_branch: str, message: str
    ) -> str:
        try:
            run_git(["checkout", "-b", new_branch], cwd=worktree_path, timeout=10)
            run_git(["add", "-A"], cwd=worktree_path)
            run_git(["commit", "-m", message], cwd=worktree_path, timeout=30)
            return run_git(["push", "-u", "origin", new_branch], cwd=worktree_path, timeout=120)
        except SubprocessError as e:
            raise WorktreeError(f"Failed to create branch and push: {e}") from e
```

- [ ] **Step 3: Run import test to verify it passes**

```bash
python -m pytest tests/unit/test_domain.py::TestPortImports::test_git_worktree_adapter_importable -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add app/adapters/worktree/
git commit -m "feat: add git worktree adapter"
```

---

## Task 7: OpenCode AI Adapter

**Files:**
- Create: `app/adapters/ai/__init__.py`
- Create: `app/adapters/ai/opencode_adapter.py`

- [ ] **Step 1: Write failing import test**

Add to `tests/unit/test_domain.py` (append to TestPortImports class):
```python
    def test_opencode_adapter_importable(self):
        from app.adapters.ai.opencode_adapter import OpenCodeAdapter  # noqa: F401
```

Run:
```bash
python -m pytest tests/unit/test_domain.py::TestPortImports::test_opencode_adapter_importable -v
```

Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 2: Implement**

Create `app/adapters/ai/__init__.py`:
```python
"""AI provider adapters."""
```

Create `app/adapters/ai/opencode_adapter.py`:
```python
"""OpenCode CLI implementation of AIProvider."""
import asyncio
import json
import logging
import os
import tempfile
from collections.abc import AsyncGenerator

from app.adapters._subprocess import clean_env
from app.domain.exceptions import ProviderError
from app.domain.models import Comment, Finding, Review
from app.ports.ai_provider import (
    AIProvider,
    FixChunkEvent,
    ReviewChunkEvent,
    ReviewResultEvent,
    ReviewStreamEvent,
)

logger = logging.getLogger(__name__)

_REVIEW_PROMPT_TEMPLATE = """You are reviewing Pull Request #{pr_number} from repository {repo_full_name}.
Analyze the attached diff file and provide a code review. For each issue found, classify it with a criticality level:
- P0: Critical - Security vulnerability, data loss, crash
- P1: Major - Bug, incorrect logic, performance issue
- P2: Minor - Code style, naming, minor improvement
- P3: Suggestion - Nice-to-have, optional improvement

Return your response as a JSON object with this exact structure:
{{"summary": "Brief overall assessment", "findings": [{{"criticality": "P0", "title": "Short title", "description": "Detailed explanation", "file": "filename if applicable", "line": "line number or range if applicable", "suggestion": "Suggested fix if applicable"}}]}}

IMPORTANT: Return ONLY the JSON object, no markdown fences, no extra text."""

_FIX_PROMPT_TEMPLATE = """You are fixing a code review comment on PR #{pr_number} in {repo_full_name}.
The reviewer left this comment:

{comment_body}

You are currently in the repository checkout on the PR branch.
Read the relevant files, understand the issue, and EDIT the files to implement the fix.
Make minimal, targeted changes. Do NOT create new files unless absolutely necessary.
Do NOT run tests or build commands — just make the code changes."""

_OPENCODE_PERMISSIONS = {
    "permissions": {
        "allow": ["Bash", "Edit", "Write", "Read", "Grep", "Glob",
                  "bash", "edit", "write", "read", "grep", "glob"],
    }
}

_OPENCODE_PROJECT_CONFIG = {
    "$schema": "https://opencode.ai/config.json",
    "agent": {
        "build": {
            "permission": {
                "bash": "allow",
                "edit": "allow",
                "write": "allow",
            }
        }
    },
}


def _parse_review_output(output: str) -> Review:
    """Parse raw AI output into a Review. Returns a fallback Review on parse failure."""
    try:
        start = output.find("{")
        end = output.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(output[start:end])
            findings = [
                Finding(
                    priority=f.get("priority", f.get("criticality", "P3")),
                    title=f.get("title", ""),
                    description=f.get("description", ""),
                    file=f.get("file"),
                    line=f.get("line"),
                    suggestion=f.get("suggestion"),
                )
                for f in data.get("findings", [])
            ]
            return Review(summary=data.get("summary", ""), findings=findings)
    except json.JSONDecodeError:
        pass

    return Review(
        summary="Review completed but output could not be parsed as structured JSON.",
        findings=[],
        raw_output=output,
        raw_length=len(output),
    )


async def _stream_opencode(
    message: str,
    context: str | None = None,
    timeout: int = 300,
    cwd: str | None = None,
) -> AsyncGenerator[str, None]:
    """Stream raw opencode output line by line."""
    prompt = message
    if context:
        prompt = f"{message}\n\n---\n\n{context}"

    context_file = None
    extra_args: list[str] = []
    if cwd:
        extra_args = ["--dir", cwd]

    try:
        if len(prompt) > 4000:
            f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False)
            f.write(prompt)
            f.close()
            context_file = f.name
            proc = await asyncio.create_subprocess_exec(
                "opencode", "run", *extra_args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=clean_env(),
            )
            with open(context_file) as fh:
                input_bytes = fh.read().encode()
            if proc.stdin is not None:
                proc.stdin.write(input_bytes)
                proc.stdin.close()
        else:
            proc = await asyncio.create_subprocess_exec(
                "opencode", "run", *extra_args, prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=clean_env(),
            )

        if proc.stdout is None or proc.stderr is None:
            raise ProviderError("opencode subprocess did not open stdout/stderr")

        async def _read_stderr() -> list[str]:
            lines = []
            while True:
                line = await proc.stderr.readline()  # type: ignore[union-attr]
                if not line:
                    break
                decoded = line.decode().rstrip()
                logger.warning("[opencode stderr] %s", decoded)
                lines.append(decoded)
            return lines

        stderr_task = asyncio.create_task(_read_stderr())

        while True:
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                yield "\n[TIMEOUT]\n"
                break
            if not line:
                break
            decoded = line.decode()
            logger.info("[opencode] %s", decoded.rstrip())
            yield decoded

        await proc.wait()
        logger.info("opencode exit code: %s", proc.returncode)

        noise = ["[STALE]", "fatal: options '--name-only'", "cannot be used together"]
        stderr_lines = await stderr_task
        filtered = [l for l in stderr_lines if not any(p in l for p in noise)]
        if filtered:
            yield "\n--- stderr ---\n"
            for err_line in filtered:
                yield err_line + "\n"

    finally:
        if context_file:
            os.unlink(context_file)


class OpenCodeAdapter(AIProvider):
    """Implements AIProvider using the opencode CLI tool."""

    async def stream_review(
        self, repo_full_name: str, pr_number: int, diff: str
    ) -> AsyncGenerator[ReviewStreamEvent, None]:
        message = _REVIEW_PROMPT_TEMPLATE.format(
            pr_number=pr_number, repo_full_name=repo_full_name
        )
        chunks: list[str] = []
        async for chunk in _stream_opencode(message, context=diff[:30000]):
            chunks.append(chunk)
            yield ReviewChunkEvent(text=chunk)

        full_output = "".join(chunks).strip()
        review = _parse_review_output(full_output)
        yield ReviewResultEvent(review=review)

    async def analyze_comments(
        self, repo_full_name: str, pr_number: int, comments: list[Comment]
    ) -> list[dict]:
        if not comments:
            return []

        comments_text = "\n\n".join(
            f"Comment by {c.author}:\n{c.body}" for c in comments
        )
        message = (
            f"Analyze the comments from PR #{pr_number} in {repo_full_name} attached in the file.\n"
            "For each comment, assess criticality (P0-P3), validity (true/false), interest (high/medium/low), "
            "and provide a summary.\n"
            'Return a JSON array: [{"author": "username", "criticality": "P0", "valid": true, '
            '"interest": "high", "summary": "Brief analysis", "original_body": "first 100 chars"}]\n'
            "IMPORTANT: Return ONLY the JSON array, no markdown fences, no extra text."
        )

        output_parts: list[str] = []
        async for chunk in _stream_opencode(message, context=comments_text[:20000]):
            output_parts.append(chunk)
        output = "".join(output_parts)

        try:
            start = output.find("[")
            end = output.rfind("]") + 1
            if start >= 0 and end > start:
                return json.loads(output[start:end])
        except json.JSONDecodeError:
            pass
        return []

    async def stream_fix(
        self, repo_dir: str, repo_full_name: str, pr_number: int, comment_body: str
    ) -> AsyncGenerator[FixChunkEvent, None]:
        self._write_opencode_config(repo_dir)

        prompt = _FIX_PROMPT_TEMPLATE.format(
            pr_number=pr_number,
            repo_full_name=repo_full_name,
            comment_body=comment_body,
        )
        async for chunk in _stream_opencode(prompt, cwd=repo_dir, timeout=600):
            yield FixChunkEvent(text=chunk)

    def _write_opencode_config(self, repo_dir: str) -> None:
        """Write opencode permission config files to the repo directory."""
        settings_dir = os.path.join(repo_dir, ".opencode")
        os.makedirs(settings_dir, exist_ok=True)
        with open(os.path.join(settings_dir, "settings.json"), "w") as f:
            json.dump(_OPENCODE_PERMISSIONS, f)

        config_path = os.path.join(repo_dir, "opencode.json")
        with open(config_path, "w") as f:
            json.dump(_OPENCODE_PROJECT_CONFIG, f, indent=2)
        logger.info("Wrote opencode.json at %s", config_path)

    async def generate_text(self, prompt: str, timeout: int = 60) -> str:
        parts: list[str] = []
        async for chunk in _stream_opencode(prompt, timeout=timeout):
            parts.append(chunk)
        result = "".join(parts).strip().strip("`").strip()
        if result and len(result) <= 500:
            return result
        raise ProviderError(f"generate_text returned unusable output (length={len(result)})")
```

- [ ] **Step 3: Run import test to verify it passes**

```bash
python -m pytest tests/unit/test_domain.py::TestPortImports::test_opencode_adapter_importable -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add app/adapters/ai/
git commit -m "feat: add OpenCode AI adapter"
```

---

## Task 8: ReviewService

**Files:**
- Create: `app/services/__init__.py`
- Create: `app/services/review_service.py`
- Create: `tests/unit/test_review_service.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_review_service.py`:
```python
"""Tests for ReviewService."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from collections.abc import AsyncGenerator

from app.domain.models import Finding, Review
from app.ports.ai_provider import ReviewChunkEvent, ReviewResultEvent
from app.services.review_service import ReviewService


def _make_review(summary: str = "LGTM") -> Review:
    return Review(summary=summary, findings=[])


async def _mock_stream(*events):
    for event in events:
        yield event


@pytest.fixture
def ai_provider():
    provider = MagicMock()
    return provider


@pytest.fixture
def cache_port():
    cache = MagicMock()
    cache.get_review.return_value = None
    return cache


@pytest.fixture
def vcs_port():
    vcs = MagicMock()
    vcs.get_diff.return_value = "--- a/foo.py\n+++ b/foo.py\n"
    return vcs


@pytest.fixture
def service(ai_provider, cache_port, vcs_port):
    return ReviewService(ai=ai_provider, cache=cache_port, vcs=vcs_port)


class TestGetOrRunReview:
    async def test_returns_cached_review(self, service, cache_port):
        cached = _make_review("cached result")
        cache_port.get_review.return_value = cached
        result = await service.get_or_run_review("acme/backend", 1)
        assert result.summary == "cached result"
        cache_port.get_review.assert_called_once_with("acme/backend", 1)

    async def test_runs_and_caches_when_no_cache(self, service, ai_provider, cache_port, vcs_port):
        review = _make_review("fresh")
        chunk = ReviewChunkEvent("some text")
        result_event = ReviewResultEvent(review)

        async def mock_stream(repo, pr, diff):
            yield chunk
            yield result_event

        ai_provider.stream_review = mock_stream
        result = await service.get_or_run_review("acme/backend", 1)
        assert result.summary == "fresh"
        cache_port.save_review.assert_called_once_with("acme/backend", 1, review)


class TestRerunReview:
    async def test_skips_cache_and_overwrites(self, service, ai_provider, cache_port, vcs_port):
        review = _make_review("new review")
        result_event = ReviewResultEvent(review)

        async def mock_stream(repo, pr, diff):
            yield ReviewChunkEvent("text")
            yield result_event

        ai_provider.stream_review = mock_stream
        result = await service.rerun_review("acme/backend", 1)
        assert result.summary == "new review"
        cache_port.get_review.assert_not_called()
        cache_port.save_review.assert_called_once()


class TestStreamReview:
    async def test_yields_chunks_and_result(self, service, ai_provider, cache_port, vcs_port):
        review = _make_review("streamed")
        events = [ReviewChunkEvent("a"), ReviewChunkEvent("b"), ReviewResultEvent(review)]

        async def mock_stream(repo, pr, diff):
            for e in events:
                yield e

        ai_provider.stream_review = mock_stream
        collected = []
        async for event in service.stream_review("acme/backend", 1):
            collected.append(event)

        assert len(collected) == 3
        assert isinstance(collected[0], ReviewChunkEvent)
        assert isinstance(collected[2], ReviewResultEvent)
        cache_port.save_review.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/unit/test_review_service.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.services'`

- [ ] **Step 3: Implement**

Create `app/services/__init__.py`:
```python
"""Service layer — orchestrates ports to implement use cases."""
```

Create `app/services/review_service.py`:
```python
"""ReviewService — orchestrates AI, VCS, and cache ports for PR review."""
from collections.abc import AsyncGenerator

from app.domain.models import Review
from app.ports.ai_provider import AIProvider, ReviewResultEvent, ReviewStreamEvent
from app.ports.cache_port import CachePort
from app.ports.vcs_port import VCSPort


class ReviewService:
    def __init__(self, ai: AIProvider, cache: CachePort, vcs: VCSPort) -> None:
        self._ai = ai
        self._cache = cache
        self._vcs = vcs

    async def get_or_run_review(self, repo_full_name: str, pr_number: int) -> Review:
        """Return cached review or run a fresh one."""
        cached = self._cache.get_review(repo_full_name, pr_number)
        if cached is not None:
            return cached
        return await self._run_review(repo_full_name, pr_number)

    async def rerun_review(self, repo_full_name: str, pr_number: int) -> Review:
        """Always run a fresh review, overwriting any cache."""
        return await self._run_review(repo_full_name, pr_number)

    async def stream_review(
        self, repo_full_name: str, pr_number: int
    ) -> AsyncGenerator[ReviewStreamEvent, None]:
        """Stream review events. Saves the Review to cache when the result event arrives."""
        diff = self._vcs.get_diff(repo_full_name, pr_number)
        async for event in self._ai.stream_review(repo_full_name, pr_number, diff):
            if isinstance(event, ReviewResultEvent):
                self._cache.save_review(repo_full_name, pr_number, event.review)
            yield event

    async def _run_review(self, repo_full_name: str, pr_number: int) -> Review:
        diff = self._vcs.get_diff(repo_full_name, pr_number)
        review: Review | None = None
        async for event in self._ai.stream_review(repo_full_name, pr_number, diff):
            if isinstance(event, ReviewResultEvent):
                review = event.review
        if review is None:
            from app.domain.models import Review as ReviewModel
            review = ReviewModel(summary="No review result received.", findings=[])
        self._cache.save_review(repo_full_name, pr_number, review)
        return review

    def clear_cache(self, repo_full_name: str, pr_number: int) -> None:
        self._cache.clear_review(repo_full_name, pr_number)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/unit/test_review_service.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/__init__.py app/services/review_service.py tests/unit/test_review_service.py
git commit -m "feat: add ReviewService with cache-or-run and streaming support"
```

---

## Task 9: FixService

**Files:**
- Create: `app/services/fix_service.py`
- Create: `tests/unit/test_fix_service.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_fix_service.py`:
```python
"""Tests for FixService."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.domain.models import FixResult
from app.ports.ai_provider import FixChunkEvent
from app.services.fix_service import FixService


@pytest.fixture
def ai_provider():
    return MagicMock()


@pytest.fixture
def vcs_port():
    vcs = MagicMock()
    vcs.get_pr_head_branch.return_value = "feature/fix"
    return vcs


@pytest.fixture
def worktree_port():
    wt = MagicMock()
    wt.create.return_value = "/tmp/worktree"
    wt.worktree_path.return_value = "/tmp/worktree"
    wt.has_changes.return_value = True
    wt.get_staged_diff.return_value = "--- a/foo.py\n+++ b/foo.py\n"
    return wt


@pytest.fixture
def service(ai_provider, vcs_port, worktree_port):
    return FixService(ai=ai_provider, vcs=vcs_port, worktree=worktree_port)


class TestStreamFix:
    async def test_yields_status_and_chunks(self, service, ai_provider, vcs_port, worktree_port):
        async def mock_fix(*args):
            yield FixChunkEvent("some output")

        ai_provider.stream_fix = mock_fix

        events = []
        async for event in service.stream_fix("acme/backend", 1, "fix this"):
            events.append(event)

        event_types = [e["type"] for e in events]
        assert "status" in event_types
        assert "chunk" in event_types
        assert "result" in event_types

    async def test_result_has_changes(self, service, ai_provider, worktree_port):
        async def mock_fix(*args):
            yield FixChunkEvent("output")

        ai_provider.stream_fix = mock_fix
        worktree_port.has_changes.return_value = True
        worktree_port.get_staged_diff.return_value = "diff content"

        events = []
        async for event in service.stream_fix("acme/backend", 1, "fix this"):
            events.append(event)

        result = next(e for e in events if e["type"] == "result")
        assert result["has_changes"] is True
        assert "diff" in result

    async def test_result_no_changes(self, service, ai_provider, worktree_port):
        async def mock_fix(*args):
            yield FixChunkEvent("output")

        ai_provider.stream_fix = mock_fix
        worktree_port.has_changes.return_value = False

        events = []
        async for event in service.stream_fix("acme/backend", 1, "fix this"):
            events.append(event)

        result = next(e for e in events if e["type"] == "result")
        assert result["has_changes"] is False


class TestPushFix:
    async def test_commit_and_push(self, service, ai_provider, worktree_port):
        async def mock_gen(*args):
            yield FixChunkEvent("dummy")

        ai_provider.stream_fix = mock_gen
        ai_provider.generate_text = AsyncMock(return_value="fix: address review")
        worktree_port.has_changes.return_value = True
        worktree_port.commit_and_push.return_value = ""

        result = await service.push_fix("acme/backend", 1, "diff", "fix this")
        assert result["status"] == "pushed"
        assert "commit_message" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/unit/test_fix_service.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.services.fix_service'`

- [ ] **Step 3: Implement**

Create `app/services/fix_service.py`:
```python
"""FixService — orchestrates AI, VCS, and worktree ports for implementing review fixes."""
import logging
import time
from collections.abc import AsyncGenerator

from app.domain.exceptions import WorktreeError
from app.ports.ai_provider import AIProvider, FixChunkEvent
from app.ports.vcs_port import VCSPort
from app.ports.worktree_port import WorktreePort

logger = logging.getLogger(__name__)

_DEFAULT_COMMIT_MSG_TEMPLATE = "fix: address review comment on PR #{pr_number}"


class FixService:
    def __init__(
        self, ai: AIProvider, vcs: VCSPort, worktree: WorktreePort
    ) -> None:
        self._ai = ai
        self._vcs = vcs
        self._worktree = worktree

    async def stream_fix(
        self, repo_full_name: str, pr_number: int, comment_body: str
    ) -> AsyncGenerator[dict, None]:
        """Stream fix implementation. Yields status/chunk/result/error/done dicts."""
        try:
            yield {"type": "status", "text": "Fetching PR info..."}
            pr_branch = self._vcs.get_pr_head_branch(repo_full_name, pr_number)

            yield {"type": "status", "text": f"Preparing worktree for PR #{pr_number}..."}
            wt_path = self._worktree.create(repo_full_name, pr_number, pr_branch)
            yield {"type": "status", "text": f"Worktree ready at {wt_path}"}

            yield {"type": "status", "text": "Running AI to implement fix..."}
            full_output: list[str] = []
            async for chunk in self._ai.stream_fix(wt_path, repo_full_name, pr_number, comment_body):
                full_output.append(chunk.text)
                yield {"type": "chunk", "text": chunk.text}

            if not self._worktree.has_changes(wt_path):
                yield {"type": "error", "text": "AI did not make any file changes."}
                yield {
                    "type": "result",
                    "worktree_path": wt_path,
                    "has_changes": False,
                    "diff": "",
                    "text": "".join(full_output).strip(),
                }
            else:
                self._worktree.stage_all(wt_path)
                diff_text = self._worktree.get_staged_diff(wt_path)
                yield {"type": "status", "text": f"Changes ready in {wt_path}"}
                yield {
                    "type": "result",
                    "worktree_path": wt_path,
                    "branch": pr_branch,
                    "has_changes": True,
                    "diff": diff_text[:10000],
                    "text": "".join(full_output).strip(),
                }

            yield {"type": "done"}

        except Exception as e:
            logger.exception("Fix flow failed")
            yield {"type": "error", "text": str(e)}
            yield {"type": "done"}

    async def push_fix(
        self,
        repo_full_name: str,
        pr_number: int,
        diff: str,
        comment_body: str,
    ) -> dict:
        """Commit and push changes to the existing PR branch."""
        wt_path = self._worktree.worktree_path(repo_full_name, pr_number)
        commit_msg = await self._generate_commit_message(diff, comment_body, pr_number)
        self._worktree.commit_and_push(wt_path, commit_msg)
        return {"status": "pushed", "commit_message": commit_msg}

    async def create_pr_from_fix(
        self,
        repo_full_name: str,
        pr_number: int,
        pr_branch: str,
        diff: str,
        comment_body: str,
    ) -> dict:
        """Create a new branch + PR from the fix changes."""
        wt_path = self._worktree.worktree_path(repo_full_name, pr_number)
        commit_msg = await self._generate_commit_message(diff, comment_body, pr_number)
        pr_title, pr_body = await self._generate_pr_description(
            pr_number, pr_branch, diff, comment_body
        )
        new_branch = f"fix/pr{pr_number}-review-{int(time.time())}"
        self._worktree.create_branch_and_push(wt_path, new_branch, commit_msg)
        pr_result = self._vcs.create_pr(repo_full_name, new_branch, pr_branch, pr_title, pr_body)
        return {
            "status": "created",
            "pr_url": pr_result["url"],
            "pr_title": pr_title,
            "commit_message": commit_msg,
            "branch": new_branch,
        }

    async def _generate_commit_message(
        self, diff: str, comment_body: str, pr_number: int
    ) -> str:
        prompt = (
            "Generate a concise git commit message (1 line subject, optional body) for the following changes. "
            "The changes address this review comment:\n\n"
            f"Review comment: {comment_body}\n\n"
            f"Diff:\n```\n{diff[:6000]}\n```\n\n"
            "Reply with ONLY the commit message, no explanation, no markdown fences."
        )
        try:
            return await self._ai.generate_text(prompt, timeout=60)
        except Exception:
            logger.warning("Failed to generate commit message with AI, using default")
            return _DEFAULT_COMMIT_MSG_TEMPLATE.format(pr_number=pr_number)

    async def _generate_pr_description(
        self,
        pr_number: int,
        pr_branch: str,
        diff: str,
        comment_body: str,
    ) -> tuple[str, str]:
        prompt = (
            "Generate a pull request title and body for the following fix. "
            "The fix addresses a review comment on an existing PR.\n\n"
            f"Original PR branch: {pr_branch}\n"
            f"Review comment: {comment_body}\n\n"
            f"Diff:\n```\n{diff[:6000]}\n```\n\n"
            "Reply in this exact format (no markdown fences around the whole thing):\n"
            "TITLE: <concise PR title>\n"
            "BODY:\n<markdown PR body with a Summary section and what was changed>"
        )
        try:
            text = await self._ai.generate_text(prompt, timeout=60)
            if "TITLE:" in text and "BODY:" in text:
                title = text.split("BODY:")[0].replace("TITLE:", "").strip()
                body = text.split("BODY:", 1)[1].strip()
                body += f"\n\n---\n<sub>Addresses review comment on #{pr_number}</sub>"
                body += "\n<sub>🤖 Generated by OpenCode</sub>"
                return title, body
        except Exception:
            logger.warning("Failed to generate PR description with AI, using default")

        default_title = f"fix: address review comment on #{pr_number}"
        default_body = (
            f"## Summary\n\nFixes review comment on PR #{pr_number}.\n\n"
            f"### Review comment\n> {comment_body[:500]}\n\n"
            f"---\n<sub>🤖 Generated by OpenCode</sub>"
        )
        return default_title, default_body
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/unit/test_fix_service.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/fix_service.py tests/unit/test_fix_service.py
git commit -m "feat: add FixService for AI-powered review fix implementation"
```

---

## Task 10: CommentService

**Files:**
- Create: `app/services/comment_service.py`
- Create: `tests/unit/test_comment_service.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_comment_service.py`:
```python
"""Tests for CommentService."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.domain.models import Comment
from app.services.comment_service import CommentService


@pytest.fixture
def ai_provider():
    return MagicMock()


@pytest.fixture
def vcs_port():
    vcs = MagicMock()
    vcs.get_comments.return_value = {
        "comments": [
            {"author": {"login": "alice"}, "body": "LGTM", "created_at": "2024-01-01T00:00:00Z"},
        ],
        "reviews": [],
        "review_comments": [],
    }
    return vcs


@pytest.fixture
def service(ai_provider, vcs_port):
    return CommentService(ai=ai_provider, vcs=vcs_port)


class TestGetComments:
    def test_normalizes_author_object(self, service, vcs_port):
        result = service.get_comments("acme/backend", 1)
        assert result["comments"][0]["author"] == "alice"

    def test_includes_review_bodies(self, service, vcs_port):
        vcs_port.get_comments.return_value = {
            "comments": [],
            "reviews": [{"author": {"login": "bob"}, "body": "Needs changes", "created_at": "2024-01-01T00:00:00Z"}],
            "review_comments": [],
        }
        result = service.get_comments("acme/backend", 1)
        assert len(result["comments"]) == 1
        assert result["comments"][0]["author"] == "bob"

    def test_includes_inline_review_comments(self, service, vcs_port):
        vcs_port.get_comments.return_value = {
            "comments": [],
            "reviews": [],
            "review_comments": [
                {"author": {"login": "carol"}, "body": "typo here", "path": "app/main.py", "line": 42, "created_at": "2024-01-01T00:00:00Z"}
            ],
        }
        result = service.get_comments("acme/backend", 1)
        assert len(result["comments"]) == 1
        assert result["comments"][0]["_inline"] is True


class TestAnalyzeComments:
    async def test_calls_ai_with_normalized_comments(self, service, ai_provider):
        ai_provider.analyze_comments = AsyncMock(return_value=[
            {"author": "alice", "criticality": "P2", "valid": True, "interest": "low", "summary": "ok"}
        ])
        result = await service.analyze_comments("acme/backend", 1)
        assert result["analysis"][0]["author"] == "alice"
        ai_provider.analyze_comments.assert_called_once()

    async def test_returns_empty_when_no_comments(self, service, vcs_port, ai_provider):
        vcs_port.get_comments.return_value = {
            "comments": [], "reviews": [], "review_comments": []
        }
        ai_provider.analyze_comments = AsyncMock(return_value=[])
        result = await service.analyze_comments("acme/backend", 1)
        assert result["analysis"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/unit/test_comment_service.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.services.comment_service'`

- [ ] **Step 3: Implement**

Create `app/services/comment_service.py`:
```python
"""CommentService — aggregates and analyzes PR comments."""
from app.domain.models import Comment
from app.ports.ai_provider import AIProvider
from app.ports.vcs_port import VCSPort


def _normalize_author(author: str | dict) -> str:
    if isinstance(author, dict):
        return author.get("login", "unknown")
    return author or "unknown"


def _normalize_comment(raw: dict, index: int) -> dict:
    return {
        "id": index,
        "author": _normalize_author(raw.get("author", "")),
        "body": raw.get("body", ""),
        "file": raw.get("path"),
        "line": raw.get("line"),
        "created_at": raw.get("created_at"),
        "_inline": raw.get("_inline", False),
    }


class CommentService:
    def __init__(self, ai: AIProvider, vcs: VCSPort) -> None:
        self._ai = ai
        self._vcs = vcs

    def get_comments(self, repo_full_name: str, pr_number: int) -> dict:
        """Fetch and normalize all comments for a PR."""
        data = self._vcs.get_comments(repo_full_name, pr_number)
        all_comments: list[dict] = []

        for c in data.get("comments", []):
            all_comments.append(c | {"author": _normalize_author(c.get("author", ""))})

        for review in data.get("reviews", []):
            if review.get("body"):
                all_comments.append(review | {"author": _normalize_author(review.get("author", ""))})

        for rc in data.get("review_comments", []):
            all_comments.append(
                rc | {"author": _normalize_author(rc.get("author", "")), "_inline": True}
            )

        return {"comments": all_comments, "raw": data}

    async def analyze_comments(self, repo_full_name: str, pr_number: int) -> dict:
        """Analyze all PR comments using the AI provider."""
        raw_data = self._vcs.get_comments(repo_full_name, pr_number)
        all_comments: list[dict] = []

        for c in raw_data.get("comments", []):
            all_comments.append(c | {"author": _normalize_author(c.get("author", ""))})
        for review in raw_data.get("reviews", []):
            if review.get("body"):
                all_comments.append(review | {"author": _normalize_author(review.get("author", ""))})
        for rc in raw_data.get("review_comments", []):
            all_comments.append(
                rc | {"author": _normalize_author(rc.get("author", "")), "_inline": True}
            )

        domain_comments = [
            Comment(
                id=i,
                author=c["author"],
                body=c.get("body", ""),
                file=c.get("path") or c.get("file"),
                line=c.get("line"),
                created_at=c.get("created_at"),
            )
            for i, c in enumerate(all_comments)
        ]

        analysis = await self._ai.analyze_comments(repo_full_name, pr_number, domain_comments)
        return {"comments": all_comments, "analysis": analysis}

    def post_comment(self, repo_full_name: str, pr_number: int, body: str) -> str:
        return self._vcs.post_comment(repo_full_name, pr_number, body)

    def post_inline_comment(
        self,
        repo_full_name: str,
        pr_number: int,
        body: str,
        path: str,
        line: int,
    ) -> dict:
        return self._vcs.post_inline_comment(repo_full_name, pr_number, body, path, line)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/unit/test_comment_service.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/comment_service.py tests/unit/test_comment_service.py
git commit -m "feat: add CommentService for fetching and analyzing PR comments"
```

---

## Task 11: Wire main.py and Delete Old Files

**Files:**
- Rewrite: `app/main.py` (thin controllers + DI wiring)
- Delete: `app/gh.py`
- Delete: `app/opencode.py`
- Delete: `app/cache.py`
- Add `create_pr` to `VCSPort` and `GitHubCLIAdapter`

> **Note:** This task wires everything together. The existing `app/main.py`, `app/gh.py`, `app/opencode.py`, and `app/cache.py` will be deleted. Run the full test suite after this task to confirm nothing regressed.

- [ ] **Step 1: Add `create_pr` to VCSPort**

Edit `app/ports/vcs_port.py` — add this abstract method:
```python
    @abstractmethod
    def create_pr(
        self,
        repo_full_name: str,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> dict:
        """Create a PR and return {'url': str}."""
        ...
```

Edit `app/adapters/vcs/github_cli_adapter.py` — add this method to `GitHubCLIAdapter`:
```python
    def create_pr(
        self,
        repo_full_name: str,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> dict:
        raw = self._run([
            "pr", "create",
            "--repo", repo_full_name,
            "--head", head_branch,
            "--base", base_branch,
            "--title", title,
            "--body", body,
        ], timeout=30)
        return {"url": raw.strip()}
```

- [ ] **Step 2: Verify tests still pass after port change**

```bash
python -m pytest tests/ -v
```

Expected: all tests PASS (new abstract method is not yet tested but doesn't break anything).

- [ ] **Step 3: Rewrite app/main.py**

Replace the full content of `app/main.py` with:
```python
"""FastAPI backend — thin controllers wired to services via dependency injection."""

import json
import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.adapters.ai.opencode_adapter import OpenCodeAdapter
from app.adapters.cache.json_file_cache import JsonFileCache
from app.adapters.vcs.github_cli_adapter import GitHubCLIAdapter
from app.adapters.worktree.git_worktree_adapter import GitWorktreeAdapter
from app.services.comment_service import CommentService
from app.services.fix_service import FixService
from app.services.review_service import ReviewService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="gh-review-tool")

# --- Dependency injection ---

_cache = JsonFileCache()
_vcs = GitHubCLIAdapter()
_ai = OpenCodeAdapter()
_worktree = GitWorktreeAdapter()

_review_service = ReviewService(ai=_ai, cache=_cache, vcs=_vcs)
_fix_service = FixService(ai=_ai, vcs=_vcs, worktree=_worktree)
_comment_service = CommentService(ai=_ai, vcs=_vcs)


def get_review_service() -> ReviewService:
    return _review_service


def get_fix_service() -> FixService:
    return _fix_service


def get_comment_service() -> CommentService:
    return _comment_service


# --- Request / Response models ---

class RepoAdd(BaseModel):
    owner: str
    name: str


class RepoRemove(BaseModel):
    full_name: str


class PublishComment(BaseModel):
    repo: str
    pr_number: int
    body: str


class PublishInlineComment(BaseModel):
    repo: str
    pr_number: int
    body: str
    path: str
    line: int


class ImplementFix(BaseModel):
    repo: str
    pr_number: int
    comment_body: str


class PushFix(BaseModel):
    repo: str
    pr_number: int
    diff: str
    comment_body: str
    branch: str


# --- Repo endpoints ---

@app.get("/api/repos")
def list_repos():
    return _cache.get_repos()


@app.post("/api/repos")
def add_repo(data: RepoAdd):
    return _cache.add_repo(data.owner, data.name)


@app.delete("/api/repos")
def remove_repo(data: RepoRemove):
    return _cache.remove_repo(data.full_name)


@app.get("/api/repos/search")
def search_repos(org: str, q: str = ""):
    try:
        return _vcs.search_repos(org, q)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- PR endpoints ---

@app.get("/api/prs/{owner}/{repo}")
def list_prs(owner: str, repo: str):
    return _cache.get_prs(f"{owner}/{repo}")


@app.post("/api/prs/{owner}/{repo}/refresh")
def refresh_prs(owner: str, repo: str):
    full_name = f"{owner}/{repo}"
    try:
        prs = _vcs.list_prs(full_name)
        prs_dicts = [
            {
                "number": pr.number,
                "title": pr.title,
                "author": pr.author,
                "branch": pr.branch,
                "base_branch": pr.base_branch,
                "additions": pr.additions,
                "deletions": pr.deletions,
                "updated_at": pr.updated_at,
                "url": pr.url,
            }
            for pr in prs
        ]
        _cache.save_prs(full_name, prs_dicts)
        return prs_dicts
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- PR detail ---

@app.get("/api/pr/{owner}/{repo}/{pr_number}")
def get_pr_detail(owner: str, repo: str, pr_number: int):
    try:
        return _comment_service.get_comments(f"{owner}/{repo}", pr_number)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Review endpoints ---

@app.post("/api/review/{owner}/{repo}/{pr_number}")
async def run_review(
    owner: str,
    repo: str,
    pr_number: int,
    svc: ReviewService = Depends(get_review_service),
):
    try:
        review = await svc.get_or_run_review(f"{owner}/{repo}", pr_number)
        return {
            "summary": review.summary,
            "findings": [
                {
                    "priority": f.priority,
                    "title": f.title,
                    "description": f.description,
                    "file": f.file,
                    "line": f.line,
                    "suggestion": f.suggestion,
                }
                for f in review.findings
            ],
            **({"raw_output": review.raw_output} if review.raw_output else {}),
            **({"raw_length": review.raw_length} if review.raw_length else {}),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/review/{owner}/{repo}/{pr_number}/rerun")
async def rerun_review(
    owner: str,
    repo: str,
    pr_number: int,
    svc: ReviewService = Depends(get_review_service),
):
    try:
        review = await svc.rerun_review(f"{owner}/{repo}", pr_number)
        return {
            "summary": review.summary,
            "findings": [
                {
                    "priority": f.priority,
                    "title": f.title,
                    "description": f.description,
                    "file": f.file,
                    "line": f.line,
                    "suggestion": f.suggestion,
                }
                for f in review.findings
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/review/{owner}/{repo}/{pr_number}/stream")
async def stream_review(
    owner: str,
    repo: str,
    pr_number: int,
    svc: ReviewService = Depends(get_review_service),
):
    """SSE endpoint that streams AI output in real-time, then emits the parsed result."""
    from app.ports.ai_provider import ReviewChunkEvent, ReviewResultEvent

    async def event_stream():
        try:
            async for event in svc.stream_review(f"{owner}/{repo}", pr_number):
                if isinstance(event, ReviewChunkEvent):
                    for line in event.text.splitlines(keepends=True):
                        yield f"data: {json.dumps({'type': 'chunk', 'text': line})}\n\n"
                elif isinstance(event, ReviewResultEvent):
                    r = event.review
                    review_dict = {
                        "summary": r.summary,
                        "findings": [
                            {
                                "priority": f.priority,
                                "title": f.title,
                                "description": f.description,
                                "file": f.file,
                                "line": f.line,
                                "suggestion": f.suggestion,
                            }
                            for f in r.findings
                        ],
                    }
                    yield f"data: {json.dumps({'type': 'result', 'review': review_dict})}\n\n"
            yield 'data: {"type": "done"}\n\n'
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n"
            yield 'data: {"type": "done"}\n\n'

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# --- Comment endpoints ---

@app.post("/api/comment/publish")
def publish_comment(data: PublishComment, svc: CommentService = Depends(get_comment_service)):
    try:
        svc.post_comment(data.repo, data.pr_number, data.body)
        return {"status": "published"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/comment/inline")
def publish_inline_comment(
    data: PublishInlineComment, svc: CommentService = Depends(get_comment_service)
):
    try:
        svc.post_inline_comment(data.repo, data.pr_number, data.body, data.path, data.line)
        return {"status": "published"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/comments/{owner}/{repo}/{pr_number}/analyze")
async def analyze_comments(
    owner: str,
    repo: str,
    pr_number: int,
    svc: CommentService = Depends(get_comment_service),
):
    try:
        return await svc.analyze_comments(f"{owner}/{repo}", pr_number)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Fix endpoints ---

@app.post("/api/comment/fix")
async def implement_fix(
    data: ImplementFix, svc: FixService = Depends(get_fix_service)
):
    async def event_stream():
        async for event in svc.stream_fix(data.repo, data.pr_number, data.comment_body):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.post("/api/comment/fix/push")
async def push_fix(data: PushFix, svc: FixService = Depends(get_fix_service)):
    wt_path = _worktree.worktree_path(data.repo, data.pr_number)
    if not os.path.isdir(wt_path):
        raise HTTPException(status_code=404, detail="Worktree not found. Run fix first.")
    if not _worktree.has_changes(wt_path):
        raise HTTPException(status_code=400, detail="No changes to commit.")
    try:
        return await svc.push_fix(data.repo, data.pr_number, data.diff, data.comment_body)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/comment/fix/new-pr")
async def submit_new_pr(data: PushFix, svc: FixService = Depends(get_fix_service)):
    wt_path = _worktree.worktree_path(data.repo, data.pr_number)
    if not os.path.isdir(wt_path):
        raise HTTPException(status_code=404, detail="Worktree not found. Run fix first.")
    if not _worktree.has_changes(wt_path):
        raise HTTPException(status_code=400, detail="No changes to commit.")
    try:
        return await svc.create_pr_from_fix(
            data.repo, data.pr_number, data.branch, data.diff, data.comment_body
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Worktree management ---

@app.get("/api/worktrees")
def list_worktrees():
    return _worktree.list_worktrees()


@app.delete("/api/worktree/{owner}/{repo}/{pr_number}")
def remove_worktree(owner: str, repo: str, pr_number: int):
    try:
        _worktree.remove(f"{owner}/{repo}", pr_number)
        return {"status": "removed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/review/{owner}/{repo}/{pr_number}/cache")
def clear_review_cache(owner: str, repo: str, pr_number: int):
    _review_service.clear_cache(f"{owner}/{repo}", pr_number)
    return {"status": "cleared"}


# --- Static files ---

Path("dist").mkdir(exist_ok=True)
app.mount("/", StaticFiles(directory="dist", html=True), name="static")
```

- [ ] **Step 4: Delete old files**

```bash
rm app/gh.py app/opencode.py app/cache.py
```

- [ ] **Step 5: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests PASS. If any test imports from `app.gh`, `app.opencode`, or `app.cache` — they don't exist in the test files we wrote, so there should be none.

- [ ] **Step 6: Verify the app starts**

```bash
python -c "from app.main import app; print('OK')"
```

Expected: `OK` (no import errors).

- [ ] **Step 7: Commit**

```bash
git add app/main.py app/ports/vcs_port.py app/adapters/vcs/github_cli_adapter.py
git rm app/gh.py app/opencode.py app/cache.py
git commit -m "feat: wire hexagonal architecture — thin main.py, delete old monolithic files"
```

---

## Self-Review

**Spec coverage check:**
- ✅ AIProvider port with `stream_review`, `analyze_comments`, `stream_fix`, `generate_text` — Task 2 + 7
- ✅ `stream_review` yields `ReviewChunkEvent | ReviewResultEvent` — Task 2
- ✅ Adapter handles internal parsing — Task 7 (`_parse_review_output` inside adapter)
- ✅ `_clean_env` deduplicated into `app/adapters/_subprocess.py` — Task 3
- ✅ `get_diff` side effect (`git add -A`) moved to explicit `stage_all()` — Task 6
- ✅ `_worktree_path` centralized — `GitWorktreeAdapter.worktree_path()` — Task 6
- ✅ `cache._review_path` no longer called from main — Task 4 hides it, Task 11 uses `clear_cache`
- ✅ `assert` in production code removed — Task 7 uses `if proc.stdin is not None` guard
- ✅ Domain models as dataclasses — Task 1
- ✅ pytest added to pyproject.toml — Task 1
- ✅ `create_pr` on VCSPort — Task 11 Step 1
- ✅ `FixService.create_pr_from_fix` uses `VCSPort.create_pr` — Task 9
- ✅ All services have tests — Tasks 8, 9, 10
- ✅ JSON cache has tests — Task 4

**No placeholders found.**

**Type consistency verified:** `ReviewStreamEvent = ReviewChunkEvent | ReviewResultEvent` defined in Task 2, used correctly in Tasks 7, 8, 11. `FixChunkEvent` defined in Task 2, used in Tasks 7, 9. `FixService.stream_fix` yields `dict` events (same structure as old SSE handler).
