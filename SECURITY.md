# Security Policy

## Trust Model

**gh-review-tool is a local developer tool.** It is designed to run on your own machine at `http://localhost:8000` and is not intended to be exposed to a network.

There is intentionally no authentication system — the server trusts the local user completely, because the local user is you.

## What This Tool Can Access

- **GitHub** — via the `gh` CLI using your own authenticated session. All
  GitHub operations are performed as you, using your token stored in the
  gh CLI keyring.
- **AI provider** (`opencode` or `claude-code`) — via the corresponding CLI
  using your own API credentials. The tool has no visibility into your AI
  provider keys; it shells out to the CLI you've already authenticated.
- **Local filesystem** — worktrees and bare clones are created under
  `~/.gh-review-tool/`. Cache data (PR lists, review results, visit
  timestamps) is stored under `.cache/` in the project directory.

The backend never stores, logs, or transmits your GitHub token or AI provider
credentials.

## AI Fix Flow — File Modification Scope

The "Implement Fix" feature lets the configured AI provider edit files in a
local git worktree under `~/.gh-review-tool/worktrees/`. For `claude-code`,
this is achieved with the `--dangerously-skip-permissions` flag so that the
fix can run without per-tool prompts. The AI is restricted to the worktree's
working directory — it has no access to your other repositories or to the
tool's own source. Always review the generated diff in the UI before pushing.

## Important: Do Not Expose to a Network

**Never run this server on a public or shared network interface.**

The following command exposes the server to all network interfaces — do not use it:
```bash
# DO NOT DO THIS
uv run uvicorn app.main:app --host 0.0.0.0
```

If exposed, any user on the network could post GitHub comments, trigger code modifications, or access repository data on your behalf.

Always use the default binding (`127.0.0.1` / localhost only):
```bash
uv run uvicorn app.main:app --reload
```

## GitHub Token Handling

The application explicitly removes `GITHUB_TOKEN` and `GH_TOKEN` from the
environment before launching any subprocess (`opencode`, `claude`, `git`,
`gh`). This forces the `gh` CLI to use its keyring-based authentication and
prevents token leakage into child processes.

See `app/adapters/_subprocess.py` for the implementation.

## Reporting a Vulnerability

If you discover a security vulnerability, please open a [GitHub Issue](https://github.com/Angelaben/my-gh-app/issues) describing the issue. For sensitive disclosures, use [GitHub's private vulnerability reporting](https://github.com/Angelaben/my-gh-app/security/advisories/new) if available.

Please include:
- A description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)
