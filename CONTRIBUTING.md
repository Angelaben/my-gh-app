# Contributing to gh-review-tool

Thank you for your interest in contributing! This document covers development setup, project architecture, and contribution guidelines.

---

## Prerequisites

- **Python 3.12+** and [`uv`](https://docs.astral.sh/uv/)
- **Node.js 18+** and `npm`
- **[gh CLI](https://cli.github.com/)** — authenticated (`gh auth login`)
- **At least one AI provider CLI** — [`opencode`](https://opencode.ai) (default)
  or [`claude`](https://docs.claude.com/claude-code), installed and in PATH

---

## Development Setup

```bash
# Clone the repo (SSH or HTTPS)
git clone git@github.com:Angelaben/my-gh-app.git
# git clone https://github.com/Angelaben/my-gh-app.git
cd my-gh-app

# Install Python dependencies (including dev: pytest, pytest-asyncio)
uv sync --extra dev

# Install frontend dependencies
cd frontend && npm install && cd ..
```

### Running in development mode

**Backend** (auto-reloads on file changes):
```bash
uv run uvicorn app.main:app --reload                  # opencode (default)
AI_PROVIDER=claude-code uv run uvicorn app.main:app --reload   # Claude Code
```

**Frontend** (hot module replacement, proxies API to :8000):
```bash
cd frontend
npm run dev
```
Then open [http://localhost:5173](http://localhost:5173) for the live
frontend, or [http://localhost:8000](http://localhost:8000) for the full built
app.

Or use `./launch.sh` to bring both up in a tmux split window — see the
[README](README.md#one-shot-dev-launcher-optional).

---

## Running Tests

**Backend unit tests:**
```bash
uv run pytest
# or with verbose output:
uv run pytest -v --tb=short
```

**Frontend tests:**
```bash
cd frontend
npm test          # run once
npm run test      # same
```

**Frontend type check:**
```bash
cd frontend
npm run check
```

**Frontend build verification:**
```bash
cd frontend
npm run build
```

---

## Project Architecture

This project follows a **hexagonal (ports & adapters)** architecture. The goal is that business logic never depends on external tools — external tools are behind interfaces.

```
app/
├── domain/          # Pure data models and exceptions — no imports from adapters
│   ├── models.py    # Finding, Review, PR, Comment, FixResult dataclasses
│   └── exceptions.py
├── ports/           # Abstract interfaces (Python ABCs)
│   ├── ai_provider.py    # AIProvider: stream_review, analyze_comments, stream_fix
│   ├── vcs_port.py       # VCSPort: list_prs, get_diff, post_comment, etc.
│   ├── cache_port.py     # CachePort: get/save repos, PRs, reviews, visits
│   └── worktree_port.py  # WorktreePort: create, remove, commit_and_push
├── adapters/        # Concrete implementations of the ports
│   ├── ai/opencode_adapter.py    # Calls the opencode CLI
│   ├── vcs/github_cli_adapter.py # Calls the gh CLI
│   ├── cache/json_file_cache.py  # JSON files on disk (~/.cache/)
│   └── worktree/git_worktree_adapter.py  # git worktree + bare clone
├── services/        # Orchestration logic (use ports, not adapters directly)
│   ├── review_service.py   # get_or_run_review, stream_review, caching
│   ├── comment_service.py  # get_enriched_comments, analyze_comments
│   └── fix_service.py      # stream_fix, push_fix, create_pr_from_fix
└── main.py          # FastAPI controllers — thin wiring of HTTP to services
```

**Frontend** (`frontend/src/`):
- `lib/api.ts` — typed fetch wrappers for all backend endpoints
- `lib/sse.ts` — Server-Sent Events handler for streaming
- `stores/` — Svelte stores for repos, PRs, reviews, UI state
- `components/` — Svelte UI components

---

## Adapting for Your Needs

The architecture is designed to make swapping providers easy:

### Replace the AI provider

Implement the `AIProvider` interface in `app/ports/ai_provider.py`, then wire it up in `app/main.py`:

```python
from app.ports.ai_provider import AIProvider

class MyCustomAIAdapter(AIProvider):
    async def stream_review(self, repo_full_name, pr_number, diff, model=None):
        ...  # call your AI API
    async def analyze_comments(self, repo_full_name, pr_number, comments):
        ...
    async def stream_fix(self, repo_dir, repo_full_name, pr_number, comment_body):
        ...
    async def generate_text(self, prompt, timeout=60):
        ...
```

### Replace the VCS provider

Implement `VCSPort` in `app/ports/vcs_port.py` to work with GitLab, Gitea, or any other platform that exposes PR/diff APIs.

---

## Submitting Changes

1. **Fork** the repository and create a branch from `master`.
2. **Write tests** for new backend logic in `tests/unit/`.
3. **Ensure all tests pass**: `uv run pytest` and `cd frontend && npm test`.
4. **Open a pull request** with a clear description of what and why.
5. **No new heavy dependencies** without prior discussion in an issue.

There is no formal code review process yet — PRs are reviewed on a best-effort basis.
