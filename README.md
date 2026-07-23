# gh-review-tool — Local AI Code Review for GitHub Pull Requests

[![CI](https://github.com/Angelaben/my-gh-app/actions/workflows/ci.yml/badge.svg)](https://github.com/Angelaben/my-gh-app/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue?logo=python&logoColor=white)
![Svelte 5](https://img.shields.io/badge/frontend-Svelte%205-orange?logo=svelte&logoColor=white)
![Self-Hosted](https://img.shields.io/badge/self--hosted-✓-success)
[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-angelaben-FFDD00?style=flat&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/angelaben)

[![Buy Me a Coffee](https://www.buymeacoffee.com/assets/img/custom_images/yellow_img.png)](https://buymeacoffee.com/angelaben)

> **Stream AI code reviews on any GitHub pull request, post inline comments, request changes, and auto-implement reviewer feedback — all from a private local dashboard.** No SaaS, no telemetry, no data leaves your machine. Bring your own `opencode` or `Claude Code` install and review PRs the way you already do on github.com — just with AI doing the first pass.

**`gh-review-tool` is a self-hosted, open-source AI code reviewer for GitHub.** It wires the `gh` CLI to the AI provider of your choice and gives you a desktop-class dashboard for browsing pull requests, getting streaming AI analysis with P0–P3 severity ratings, publishing inline review comments or full *Request Changes* reviews, and implementing fixes directly on the PR branch — without ever uploading a diff to a third-party SaaS.

### ✨ Highlights

- ⚡ **Streaming AI code reviews** — Server-Sent Events render the analysis as the model produces it; no waiting for a giant blob to land at the end.
- 🎯 **Severity-classified findings** — every issue is tagged P0 (critical) → P3 (suggestion) so you can triage at a glance.
- 💬 **Inline & PR-level comments** — publish each finding as a GitHub inline review comment on the exact `path:line`, or as a top-level PR comment. P1 findings can be promoted to a single-finding *Request Changes* review.
- 📦 **Batched Request Changes reviews** — stage multiple findings, then ship them as one GitHub review with state `REQUEST_CHANGES` and inline comments per finding.
- 🔁 **One-click AI fix flow** — let the AI apply the suggested change in an isolated git worktree, review the diff, then push to the PR branch or open a follow-up PR.
- 🧠 **Comment intelligence** — re-classify existing PR comments by criticality, validity and interest, and detect new replies since your last visit.
- 🔌 **Hexagonal architecture (ports & adapters)** — swap any external dependency (AI provider, VCS host, cache backend) by implementing one Python interface.
- 🔒 **Private by design** — runs on `localhost`, auth is delegated to the `gh` CLI keyring, and `GITHUB_TOKEN` / `GH_TOKEN` are stripped from every subprocess env to prevent token leakage.
- 💸 **No subscription, no rate limits beyond your provider's** — bring your own AI CLI; pay only what your `opencode` / `Claude Code` plan costs.

---

## Table of Contents

- [Who is this for?](#who-is-this-for)
- [How it compares to hosted AI review tools](#how-it-compares-to-hosted-ai-review-tools)
- [Prerequisites](#prerequisites)
- [Installation (Docker, default)](#installation-docker-default)
- [Native install (without Docker)](#native-install-without-docker)
- [Usage](#usage)
- [Configuration](#configuration)
- [Claude Code model notes](#claude-code-model-notes)
- [Adapting for Your Needs](#adapting-for-your-needs)
- [Updating](#updating)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)
- [Contributing](#contributing)
- [Security](#security)
- [Support](#support)

---

## Who is this for?

`gh-review-tool` is built for developers and small teams who want **AI-assisted GitHub code review without sending diffs to a third-party SaaS**. Typical users:

- **Solo maintainers** triaging community PRs faster.
- **Engineers in regulated environments** (finance, health, defense) where source code can't leave the laptop or VPC.
- **Teams already paying for `opencode` or `Claude Code`** who want to amortize that subscription across PR reviews.
- **Hexagonal-architecture enthusiasts** who want to plug in their own AI / VCS / cache backends.

If you want a hosted SaaS that auto-reviews every PR — this is not that. See [How it compares](#how-it-compares-to-hosted-ai-review-tools) below.

---

## How it compares to hosted AI review tools

| | gh-review-tool | Hosted AI review SaaS |
|---|---|---|
| Where reviews run | **Your machine** | Vendor's cloud |
| Diff upload required | ❌ Never | ✅ Always |
| Subscription | Pay your AI CLI provider only | Per-seat / per-repo monthly fee |
| AI model choice | Any model your CLI supports (`opus`, `sonnet`, `haiku`, custom Bedrock IDs, …) | Vendor-curated short list |
| Triggered by | You, on demand | Webhook on every PR |
| Review style | Streaming, severity-tagged, with inline + Request-Changes flows | Bulk comment per PR |
| Source code access | Local-only via `gh` CLI keyring | OAuth GitHub App with broad repo scopes |
| Self-hostable | ✅ It already is | ❌ Closed source |
| Telemetry | None | Often enabled by default |

**TL;DR:** if you want zero-config "auto-review every PR in our org" — pick a SaaS. If you want **opt-in, private, AI-powered code review on your terms**, you want this tool.

---

## Prerequisites

- **[Docker](https://docs.docker.com/get-docker/)** with the Compose plugin — the default, recommended way to run the tool (see below). Or, for a native install: **Python 3.12+** + [`uv`](https://docs.astral.sh/uv/), and **Node.js 18+** + `npm`.
- **[GitHub CLI (`gh`)](https://cli.github.com/)** — authenticated via `gh auth login` **on the host** (the container mounts this auth rather than logging in itself).
- **An AI CLI provider**, also authenticated on the host — at least one of:
  - **[opencode CLI](https://opencode.ai)** — default provider, supports OpenRouter / Anthropic / OpenAI / local models
  - **[Claude Code CLI](https://docs.claude.com/claude-code)** — uses the
    universal model aliases (see [Claude Code model notes](#claude-code-model-notes))

> **Heads up:** authentication is handled entirely by the `gh` CLI keyring and the AI provider's own login flow. The tool itself never reads or stores tokens.

---

## Installation (Docker, default)

The tool ships a `Dockerfile` and `docker-compose.yml` that bundle the Python backend, the built frontend, `git`, `gh`, and both AI CLIs (`opencode` + `claude`) into one image. Credentials are **not** baked in — they're bind-mounted read-only from your host, so authenticate natively first:

```bash
gh auth login          # GitHub CLI
opencode auth login    # and/or: claude   (interactive login)
```

Then build and run:

```bash
# Choose either SSH or HTTPS:
git clone git@github.com:Angelaben/my-gh-app.git
# git clone https://github.com/Angelaben/my-gh-app.git
cd my-gh-app

./docker-launch.sh                          # default provider (opencode), port 8000
./docker-launch.sh --provider claude-code   # use Claude Code
./docker-launch.sh --port 9000              # custom host port
./docker-launch.sh -d                       # detached (background)
./docker-launch.sh --down                   # stop and remove the container
./docker-launch.sh --help                   # full usage
```

`docker-launch.sh` is a thin wrapper around `docker compose`; `docker compose up --build` works identically if you'd rather call it directly. Open **<http://localhost:8000>**.

App data (cache, settings, metrics) and git worktrees persist in named Docker volumes (`gh-review-cache`, `gh-review-worktrees`) across restarts and rebuilds. See the comments in `docker-compose.yml` if your `opencode`/Claude Code config lives at a non-default path, or if you only use one of the two providers and want to drop the other's mount.

### Updating (Docker)

```bash
git pull
./docker-launch.sh   # rebuilds the image and restarts
```

---

## Native install (without Docker)

Prefer running directly on the host — e.g. for development, or without Docker available? The tool still supports that:

```bash
# Choose either SSH or HTTPS:
git clone git@github.com:Angelaben/my-gh-app.git
# git clone https://github.com/Angelaben/my-gh-app.git
cd my-gh-app

# Install Python dependencies
uv sync

# Build the Svelte frontend
cd frontend
npm install
npm run build
cd ..

# Start the server (auto-detects the first AI CLI on PATH; opencode preferred)
uv run uvicorn app.main:app --reload

# …or start with Claude Code as the active AI provider
AI_PROVIDER=claude-code uv run uvicorn app.main:app --reload
```

Open **<http://localhost:8000>** in your browser. That's it — no config files, no signup, no API keys to paste.

### One-shot dev launcher (optional)

If you have `tmux` installed, `./launch.sh` boots backend + frontend dev server in a single split window — handy for development, since it runs the backend with `--reload` and the frontend as a hot-reloading Vite dev server rather than a prebuilt bundle:

```bash
./launch.sh                          # default provider (opencode)
./launch.sh --provider claude-code   # use Claude Code
./launch.sh --help                   # full usage
```

### Switching AI providers at runtime

Pick the provider from the in-app picker, **or** set it at server startup via the `AI_PROVIDER` env var. No restart required when switching from the UI:

| Value | CLI required on `PATH` | Default? |
|------|----------------------|----------|
| `opencode` | `opencode` | ✅ default |
| `claude-code` | `claude` | ✅ supported — auto-selected when `opencode` is missing but `claude` is installed |

Both providers implement the same `AIProvider` port (see `app/ports/ai_provider.py`) and are interchangeable for review, comment analysis, and fix flows. Models are listed dynamically per provider.

---

## Usage

1. **Add a repo** — click *Add Repo* in the sidebar, search by `org/name`, and add it.
2. **Browse open PRs** — select a repo to see its open pull requests.
3. **Run a streaming review** — open a PR and click *▶ Run Review* to get an AI analysis streamed token-by-token.
4. **Triage findings** — the *Review* tab lists findings by severity (P0 → P3) with file/line context.
5. **Publish to GitHub** — for each finding choose:
   - `↗ Publish` → posts an inline review comment on the exact `path:line`, or a top-level PR comment if no line is available.
   - `⚠ Request changes` *(P1 default)* → posts a single-finding GitHub review with state `REQUEST_CHANGES`.
   - `+ Add to review` → stage the finding for a batched review.
6. **Batch-publish a Request Changes review** — once you've staged findings, click *⚠ Publish reviews* at the bottom of the list to ship them all in one GitHub review object.
7. **Auto-implement fixes** — on the *Comments* tab, click *⚡ Generate Fix* on any reviewer comment. The AI works in an isolated git worktree; you then either *↑ Push to PR branch* or *+ New PR*.
8. **Live review (optional)** — open the *Live* tab and press *▶ Start live review*. While running, the tool polls every registered repo (default every 5 minutes, configurable in Settings) and automatically reviews any open non-draft PR that has no review yet or received new commits since its last review. Triggered reviews land in each PR's *Review* tab as usual, and the tab's activity feed shows every poll and trigger. Pressing *■ Stop* only stops the polling — reviews already in flight run to completion. When stopped, the tool does not poll GitHub at all.

---

## Configuration

No environment variables are required. Authentication is handled entirely by the `gh` CLI and the AI provider's own configuration (`opencode auth` or `claude` login).

Optional environment variables:

| Variable                  | Default     | Purpose                                                |
|---------------------------|-------------|--------------------------------------------------------|
| `AI_PROVIDER`             | auto-detect | Selects the AI backend at startup. Supported: `opencode`, `claude-code`. Live-switchable from the UI. |
| `REVIEW_DIFF_MAX_CHARS`   | `150000`    | Threshold above which the PR diff is split by file and reviewed in parallel sub-calls (each sub-call carries the full PR file manifest for cross-file context). Sized so most PRs fit a single coherent call; lower it for small-context models. |
| `REVIEW_MAX_CONCURRENCY`  | `3`         | Maximum number of parallel sub-reviews launched when a diff is split. Each spawns one provider subprocess. |
| `REVIEW_SYNTHESIS`        | `true`      | After a split review, run one extra AI pass that consolidates the per-chunk findings (semantic dedupe, consistent priorities, one coherent summary). Set `0`/`false` to keep the mechanical merge only. |
| `LIVE_REVIEW_POLL_INTERVAL` | `300`     | Seconds between two Live Review poll cycles (the opt-in background watcher started from the *Live* tab). Each cycle runs one `gh pr list` per registered repo. Also configurable from Settings. |

Data is stored in:

- `.cache/` — cached PR lists, review results, and visit timestamps (relative to the project directory).
- `~/.gh-review-tool/` — bare git clones and worktrees used by the AI fix flow.

See [`.env.example`](.env.example) for the full list of optional variables.

---

## Claude Code model notes

Claude Code does not expose a `models` listing command, so the model picker
shows the **universal aliases** — `fable`, `opus`, `sonnet`, `haiku` — which
the CLI maps to the correct backend-specific model ID automatically
(Anthropic API, AWS Bedrock, Vertex AI, …).

To pin a specific version, use the picker's free-form **Custom…** input and
enter the exact ID your account expects — e.g. the Bedrock EU inference
profile `eu.anthropic.claude-sonnet-4-5-20250929-v1:0`. When the CLI rejects
a model and suggests an alternative ("Try `--model` to switch to …"), the
suggestion is surfaced as a hint in the review output.

---

## Adapting for Your Needs

The project uses a strict **hexagonal (ports & adapters) architecture**, so swapping any external dependency takes one new class.

### Use a different AI provider

Implement `AIProvider` (`app/ports/ai_provider.py`) and wire it in `app/main.py`:

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

### Use a different VCS provider (GitLab, Gitea, Bitbucket, …)

Implement `VCSPort` (`app/ports/vcs_port.py`). Anything that exposes PRs/MRs and inline-comment APIs is a drop-in fit.

### Use a different cache backend

Implement `CachePort` (`app/ports/cache_port.py`) to store data in PostgreSQL, Redis, SQLite, or any other backend instead of the default local JSON files.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full architecture overview, including service-layer boundaries and SSE event contracts.

---

## Updating

**Docker:**
```bash
git pull
./docker-launch.sh
```

**Native:**
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

Release notes for every version live in [`CHANGELOG.md`](CHANGELOG.md).

---

## Troubleshooting

**`opencode: command not found` / `claude: command not found`**
Ensure the AI provider CLI for your active `AI_PROVIDER` is installed and on your `PATH`. Run `which opencode` or `which claude` to verify. The server boots even if the CLI is missing — the picker will surface a red badge so you can install it without restarting.

**`gh: command not found` or authentication errors**
Install the GitHub CLI and authenticate: `gh auth login`. The tool delegates *all* GitHub auth to `gh`, so once `gh pr list` works the tool will too.

**Streaming review hangs or produces no output**
The AI provider may be waiting for credentials. For `opencode`, run `opencode auth` once. For `claude-code`, run `claude` interactively to complete login.

**`Failed to create review: HTTP 422`**
This usually means an inline comment line is outside the PR diff, or you're trying to *Request Changes* on your own PR (GitHub forbids self-`REQUEST_CHANGES`). The adapter falls back automatically — check the server log for the underlying GitHub message.

**Port already in use**
Docker: `./docker-launch.sh --port 8001`. Native: `uv run uvicorn app.main:app --port 8001`.

**Docker: reviews fail with "CLI not found" or auth errors**
The image bundles `opencode` and `claude`, but not their credentials. Authenticate natively on the host first (`opencode auth login` / `claude`) so `docker-compose.yml`'s bind mounts have something to mount — Docker silently creates an empty directory for a mount source that doesn't exist yet, which looks like a fresh, logged-out install inside the container. Same for `gh`: run `gh auth login` on the host before starting the container.

**Worktree errors or stale fix sessions**
Worktrees can be deleted via the in-UI worktree manager, or manually:

```bash
rm -rf ~/.gh-review-tool/worktrees/<name>
```

**Corrupt or stale cache**
Clear the cache directory:

```bash
rm -rf .cache/
```

---

## FAQ

**Is `gh-review-tool` a hosted SaaS like CodeRabbit, Greptile, or Codium?**
No. It runs entirely on `localhost`. There is no vendor backend and no account to sign up for. Diffs are read from your local `gh` CLI and sent only to the AI provider you've explicitly configured.

**Does my source code leave my machine?**
Only insofar as your AI provider needs it. The tool's job is to assemble a review prompt and pipe it to your `opencode` or `Claude Code` install. Whether *that* CLI sends the prompt to a remote model (Anthropic, OpenAI, Bedrock, Vertex, OpenRouter…) or to a local one (Ollama, LM Studio, vLLM…) is entirely your provider's configuration.

**Which AI providers are supported out of the box?**
[`opencode`](https://opencode.ai) and [Claude Code](https://docs.claude.com/claude-code). Adding another provider is one new `AIProvider` subclass — see [Adapting for Your Needs](#adapting-for-your-needs).

**Which models can I use?**
Anything your AI CLI supports. `opencode` exposes its registered model list via `opencode models`; Claude Code accepts the universal aliases `fable`, `opus`, `sonnet`, `haiku` plus any pinned ID your account has access to (including AWS Bedrock inference profiles).

**Can I use it with GitLab, Bitbucket, or Gitea?**
Not out of the box. The default `VCSPort` adapter shells out to the GitHub CLI. Implementing a GitLab or Gitea adapter is a few hundred lines — see `app/ports/vcs_port.py` for the surface area.

**Does it auto-review every PR like a webhook bot?**
Not by default. Reviews run on demand, when *you* click *Run Review*. If you want continuous coverage, the opt-in **Live Review** tab polls your registered repos (default every 5 minutes) while you keep it started and auto-reviews new or updated non-draft PRs — no webhook, no inbound network exposure, and nothing polls GitHub once you press Stop.

**Why do P1 findings publish as a *Request Changes* review instead of a plain comment?**
A P1 (Major) issue is something a human reviewer would block the PR on. Promoting it to a `REQUEST_CHANGES` review surfaces the *Changes requested* banner on the PR so the author can't merge until the issue is addressed. P0/P2/P3 findings publish as regular inline comments.

**How is this different from running `claude` or `opencode` against a diff manually?**
You get: a UI for browsing PRs, streaming output, structured P0–P3 findings with inline-comment publishing, batched *Request Changes* reviews, and a one-click "implement fix" flow that runs in an isolated git worktree and pushes back to the PR. None of that exists in the raw CLI.

**Where do I report a bug or request a feature?**
[Open an issue](https://github.com/Angelaben/my-gh-app/issues) on this repository.

---

## Contributing

Pull requests welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, architecture overview, test layout, and the conventions for `Finding` / `Review` / SSE event types.

---

## Security

This is a **local-only tool** — it is not designed to be exposed on a network. Authentication is delegated entirely to the `gh` CLI keyring; `GITHUB_TOKEN` and `GH_TOKEN` are stripped from every subprocess environment. See [SECURITY.md](SECURITY.md) for the full security model and vulnerability reporting instructions.

---

## Support

If `gh-review-tool` saves you time on PR reviews, consider buying me a coffee ☕ — it directly funds time spent on this project.

---

<sub>Keywords: AI code review, GitHub PR review tool, AI pull request reviewer, self-hosted code review, local AI code reviewer, opencode GitHub integration, Claude Code GitHub PRs, AI-assisted PR review, GitHub CLI dashboard, automated code review without SaaS.</sub>
