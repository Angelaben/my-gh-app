# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
This project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **`claude-code` is re-enabled as a first-class provider.** The
  `ENABLE_CLAUDE_CODE` feature flag is removed: the provider is always
  registered, listed in the picker, and selectable via `AI_PROVIDER` or the
  UI. Availability is reported per provider by checking the `claude` binary
  on `PATH`, exactly like `opencode`. The "experimental" badge and warning
  were removed from the provider picker.
- **Claude Code model list now includes `fable`.** The model picker exposes
  the universal aliases `fable`, `opus`, `sonnet`, `haiku`; pinned IDs
  (including Bedrock inference profiles) still go through the free-form
  custom input.

### Added
- **Hexagonal AI-adapter base class.** A new `BaseCLIAIAdapter`
  (`app/adapters/ai/_base.py`) consolidates the subprocess / streaming /
  parsing plumbing that used to be duplicated across `OpenCodeAdapter` and
  `ClaudeCodeAdapter`. Adding a new CLI-backed provider is now ~50 lines
  (one `build_invocation` method plus optional hooks) instead of
  ~250 lines of copy-paste, and any improvement to the streaming loop
  benefits both adapters at once.
- **Robust subprocess cleanup.** When the consumer disconnects mid-stream
  (SSE client closed, request cancelled), the adapter now reaps the
  subprocess, cancels the stderr reader, and releases the stdin pipe with
  bounded waits — no more lingering `claude` / `opencode` processes per
  abandoned request.
- **Concurrent stdin draining.** Large opencode prompts (>64 KB) used to
  risk deadlocking on the kernel pipe buffer because the synchronous
  `write()` ran before the read loop started consuming stdout. The new
  `_drain_stdin` helper writes in a concurrent task and `await drain()`s,
  with broken-pipe tolerance.

### Fixed
- **Claude Code model-name passing** — the historical big blocker for the
  connector. `normalize_model_name()` now handles every shape the picker
  emits: opencode-style `provider/model` prefixes are stripped
  (`anthropic/claude-sonnet-4-6` → `claude-sonnet-4-6`), universal aliases
  (`opus`, `sonnet`, `haiku`), versioned IDs (`claude-opus-4-7`), and AWS
  Bedrock inference profiles
  (`eu.anthropic.claude-sonnet-4-5-20250929-v1:0`) all reach the CLI in the
  exact form it expects. Blank / whitespace-only input is treated as "use
  the default" instead of being forwarded as `--model ''` and rejected by
  the CLI's strict validation path.
- **Actionable failure hints from Claude Code.** When the CLI rejects a
  model and replies *"Try `--model` to switch to X"*, the suggested ID is
  now surfaced as a structured `[hint]` warning the UI can promote to a
  one-click switch. When the CLI says the model is unavailable without
  suggesting an alternative, a separate hint nudges the user toward the
  Bedrock inference-profile format / universal aliases — but only when the
  failure phrasing is anchored to a model identifier, so unrelated
  "configuration file may not exist" errors don't get a misleading tip.
- `generate_text` no longer silently drops stderr lines from the underlying
  CLI; they're logged at WARNING level so commit-message / PR-description
  failures stay debuggable.

### Changed
- **`claude-code` provider is now gated behind `ENABLE_CLAUDE_CODE`** and is
  no longer registered by default. The integration is still rough enough
  (model discovery quirks, Bedrock inference profile errors, multi-stage
  fallbacks on the reviews API) that we don't want a fresh install pointing
  at it out of the box. Set `ENABLE_CLAUDE_CODE=1` (or `true` / `yes` /
  `on`) to opt back in. Setting `AI_PROVIDER=claude-code` while the flag
  is off logs a warning and falls back to auto-detect; trying to switch
  to it from the UI returns a `400` with a message explaining how to
  enable it. See the new "Claude Code is disabled by default" section in
  the README for the full rationale.

### Added
- **Runtime AI provider picker** — a modal lets the user switch between
  `opencode` and `claude-code` without restarting the server or setting an
  env var. The active provider is reflected immediately in the topbar and
  the model selector is refreshed automatically.
- **Provider availability detection** at startup — the server checks each
  provider's CLI on `PATH` (`opencode`, `claude`) and logs a warning if
  the active one is missing. The warning is also surfaced as a toast in
  the UI.
- `GET /api/providers` — returns active provider, env-var origin, supported
  list, and per-provider availability.
- `POST /api/provider` — switches the active provider for the running
  server. Responds with a `warning` field when the picked provider's CLI
  is not on `PATH`.
- `httpx` added as a dev dependency (used by FastAPI's `TestClient` for
  the new endpoint tests).
- **Claude Code** as a selectable AI provider (`AI_PROVIDER=claude-code`)
- Active AI provider surfaced in the UI alongside a model picker for both
  providers, persisted to localStorage
- `launch.sh` — one-shot dev launcher that boots backend + frontend in a
  tmux split window
- CONTRIBUTING.md with development setup and architecture guide
- SECURITY.md documenting the local-only trust model
- CHANGELOG.md
- GitHub Actions CI workflow (backend on Python 3.12/3.13, frontend tests +
  build)
- GitHub issue templates (bug report, feature request)
- GitHub pull request template
- `.env.example` documenting that no environment variables are required

### Changed
- The provider badge in the topbar is now an always-visible button —
  clicking it opens the provider picker. It turns red with a `!` when the
  active provider's CLI is missing.
- The model selector is now always rendered (even before the provider
  status loads). When no provider has been resolved yet, it's disabled
  with a placeholder; otherwise it offers the provider's models plus a
  "<provider> default" option that omits the `--model` flag entirely.
- `AI_PROVIDER` is no longer required: when unset, the server picks the
  first provider whose CLI is on `PATH`.
- `/api/config` now also returns a nested `providers` block (active,
  available, from_env, supported, clis) for richer frontend state.
- README: added feature highlights, table of contents, CI badge, AI-provider
  switching guide, configuration table, troubleshooting, and "adapting for
  your needs" guide
- `.gitignore`: added `.env`, `build/`, `*.egg-info/` patterns

### Fixed
- Topbar no longer hides the provider badge / model selector when
  `/api/config` returns an empty active provider — both are always visible
  so the user can recover via the picker.
- `/api/models` no longer 500s when the active provider's CLI is missing;
  it returns an empty model list and a `warning` field.
- Claude Code review API failing on opencode-style `provider/model` names —
  the provider prefix is now stripped before being passed to the `claude` CLI

### Removed
- Internal implementation-plan documents under `docs/superpowers/` (not
  intended for end users)

---

## [0.1.0] — 2026-04-25

Initial public release.

### Added
- Local web dashboard for reviewing GitHub PRs with AI powered by [opencode](https://opencode.ai)
- Streaming AI code review with Server-Sent Events (real-time output)
- PR comment browsing with threading, reply grouping, and new-comment tracking
- AI-powered comment analysis (criticality, validity, interest)
- Fix implementation via git worktrees — commit directly to PR branch or open a new PR
- Hexagonal architecture (ports / adapters / services / domain)
- Dynamic model selection from opencode's available models
- OpenCode stderr warning surfacing in the frontend
- Stop buttons for analyze-comments and fix-implementation flows
- Model selector persisted to localStorage
- Detection and deletion of comments generated by gh-review-tool
- Footer appended to published comments identifying the tool and model used
- Fallback to GitHub API raw diff for merged/closed PRs
- MIT License
