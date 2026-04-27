# gh-review-tool

[![CI](https://github.com/Angelaben/my-gh-app/actions/workflows/ci.yml/badge.svg)](https://github.com/Angelaben/my-gh-app/actions/workflows/ci.yml)
[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-angelaben-FFDD00?style=flat&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/angelaben)
![Python](https://img.shields.io/badge/python-3.12+-blue)
![Svelte](https://img.shields.io/badge/frontend-Svelte%205-orange)
![License](https://img.shields.io/badge/license-MIT-green)

A local web dashboard for reviewing GitHub PRs with AI — pluggable backend, supports [opencode](https://opencode.ai) and [Claude Code](https://docs.claude.com/claude-code).

Add repos, browse open PRs, get AI code reviews streamed in real-time, post comments, and auto-implement fixes directly on the PR branch.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Usage](#usage)
- [Configuration](#configuration)
- [Adapting for Your Needs](#adapting-for-your-needs)
- [Updating](#updating)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [Security](#security)
- [Support](#support)

---

## Prerequisites

- **Python 3.12+** and [`uv`](https://docs.astral.sh/uv/)
- **Node.js 18+** and `npm`
- **[gh CLI](https://cli.github.com/)** — authenticated (`gh auth login`)
- **An AI CLI provider** — at least one of:
  - **[opencode CLI](https://opencode.ai)** — default provider
  - **[Claude Code CLI](https://docs.claude.com/claude-code)** — set `AI_PROVIDER=claude-code`

---

## Installation

```bash
git clone git@github.com:Angelaben/my-gh-app.git
cd my-gh-app

# Install Python dependencies
uv sync

# Build the frontend
cd frontend
npm install
npm run build
cd ..

# Start the server (default provider: opencode)
uv run uvicorn app.main:app --reload

# …or start with Claude Code as the provider
AI_PROVIDER=claude-code uv run uvicorn app.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

### Switching AI providers

The provider is selected at server startup via the `AI_PROVIDER` environment
variable:

| Value | CLI required in PATH |
|------|----------------------|
| `opencode` *(default)* | `opencode` |
| `claude-code` | `claude` |

Both providers implement the same `AIProvider` port (see
`app/ports/ai_provider.py`) and are interchangeable for review, comment
analysis, and fix flows.

---

## Usage

1. **Add a repo** — click "Add Repo" in the sidebar, search by org/name, and add it
2. **Browse PRs** — select a repo to see its open pull requests
3. **Run a review** — open a PR and click "Run Review" to get a streaming AI analysis
4. **Review tab** — see findings by severity, post them as inline or PR-level comments on GitHub
5. **Comments tab** — analyze existing PR comments, and use "Implement Fix" to let the AI provider apply the fix in a local worktree
6. **Push fixes** — review the generated diff and push directly to the PR branch, or open a new PR

---

## Configuration

No environment variables are required. Authentication is handled entirely by the `gh` CLI and `opencode`'s own configuration.

Data is stored in:
- `.cache/` — PR lists, review results, visit timestamps (relative to the project directory)
- `~/.gh-review-tool/` — bare git clones and worktrees used for fix implementation

See [`.env.example`](.env.example) for details on optional settings.

---

## Adapting for Your Needs

The project uses a **hexagonal (ports & adapters)** architecture, making it straightforward to swap out any external dependency.

### Use a different AI provider

Implement the `AIProvider` interface (`app/ports/ai_provider.py`) and wire it in `app/main.py`:

```python
from app.ports.ai_provider import AIProvider

class MyClaudioAdapter(AIProvider):
    async def stream_review(self, repo_full_name, pr_number, diff, model=None):
        ...  # call your AI API and yield ReviewChunkEvent / ReviewResultEvent
    async def analyze_comments(self, repo_full_name, pr_number, comments):
        ...
    async def stream_fix(self, repo_dir, repo_full_name, pr_number, comment_body):
        ...
    async def generate_text(self, prompt, timeout=60):
        ...
```

### Use a different VCS provider

Implement `VCSPort` (`app/ports/vcs_port.py`) to work with GitLab, Gitea, or any platform that exposes PR/diff APIs.

### Use a different cache backend

Implement `CachePort` (`app/ports/cache_port.py`) to store data in a database, Redis, or any other backend instead of local JSON files.

See [CONTRIBUTING.md](CONTRIBUTING.md) for a full architecture overview.

---

## Updating

```bash
git pull

uv sync

cd frontend
npm install
npm run build
cd ..

# Restart the server
uv run uvicorn app.main:app --reload
```

---

## Troubleshooting

**`opencode: command not found`**
Ensure `opencode` is installed and in your `PATH`. Run `which opencode` to verify.

**`gh: command not found` or authentication errors**
Install the GitHub CLI and authenticate: `gh auth login`

**Streaming review hangs or produces no output**
`opencode` may be waiting for model API credentials. Check `opencode`'s own auth configuration.

**Port already in use**
Change the port: `uv run uvicorn app.main:app --port 8001`

**Worktree errors or stale fix sessions**
Worktrees can be deleted via the UI (worktree manager icon), or manually:
```bash
rm -rf ~/.gh-review-tool/worktrees/<name>
```

**Corrupt or stale cache**
Clear the cache directory:
```bash
rm -rf .cache/
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, architecture details, and guidelines for submitting changes.

---

## Security

This is a **local-only tool** — it is not designed to be exposed on a network. See [SECURITY.md](SECURITY.md) for the full security model and vulnerability reporting instructions.

---

## Support

If this tool saves you time, consider buying me a coffee ☕

[![Buy Me a Coffee](https://www.buymeacoffee.com/assets/img/custom_images/yellow_img.png)](https://buymeacoffee.com/angelaben)
