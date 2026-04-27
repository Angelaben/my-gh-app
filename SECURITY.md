# Security Policy

## Trust Model

**gh-review-tool is a local developer tool.** It is designed to run on your own machine at `http://localhost:8000` and is not intended to be exposed to a network.

There is intentionally no authentication system — the server trusts the local user completely, because the local user is you.

## What This Tool Can Access

- **GitHub** — via the `gh` CLI using your own authenticated session. All GitHub operations are performed as you, using your token stored in the gh CLI keyring.
- **opencode** — via the `opencode` CLI using your own API credentials. The tool has no visibility into your AI provider keys.
- **Local filesystem** — worktrees and bare clones are created under `~/.gh-review-tool/`. Cache data (PR lists, review results) is stored under `.cache/` in the project directory.

The backend never stores, logs, or transmits your GitHub token or AI provider credentials.

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

The application explicitly removes `GITHUB_TOKEN` and `GH_TOKEN` from the environment before launching any subprocess (`opencode`, `git`). This forces the `gh` CLI to use its keyring-based authentication and prevents token leakage into child processes.

See `app/adapters/_subprocess.py` for the implementation.

## Reporting a Vulnerability

If you discover a security vulnerability, please open a [GitHub Issue](https://github.com/Angelaben/my-gh-app/issues) describing the issue. For sensitive disclosures, use [GitHub's private vulnerability reporting](https://github.com/Angelaben/my-gh-app/security/advisories/new) if available.

Please include:
- A description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)
