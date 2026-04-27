# gh-review-tool

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-angelaben-FFDD00?style=flat&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/angelaben)
![Python](https://img.shields.io/badge/python-3.12+-blue)
![Svelte](https://img.shields.io/badge/frontend-Svelte%205-orange)
![License](https://img.shields.io/badge/license-MIT-green)

A local web dashboard for reviewing GitHub PRs with AI — pluggable backend, supports [opencode](https://opencode.ai) and [Claude Code](https://docs.claude.com/claude-code).

Add repos, browse open PRs, get AI code reviews streamed in real-time, post comments, and auto-implement fixes directly on the PR branch.

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

Worktrees and bare clones are stored in `~/.gh-review-tool/`.

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

## Support

If this tool saves you time, consider buying me a coffee ☕

[![Buy Me a Coffee](https://www.buymeacoffee.com/assets/img/custom_images/yellow_img.png)](https://buymeacoffee.com/angelaben)
