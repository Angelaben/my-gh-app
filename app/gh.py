"""Wrapper around the gh CLI for GitHub interactions."""

import json
import os
import subprocess


def _clean_env() -> dict[str, str]:
    """Return a copy of the environment without GITHUB_TOKEN so gh uses keyring auth."""
    env = os.environ.copy()
    env.pop("GITHUB_TOKEN", None)
    env.pop("GH_TOKEN", None)
    return env


def _run(args: list[str]) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60, env=_clean_env())
    if result.returncode != 0:
        raise RuntimeError(f"gh command failed: {result.stderr.strip()}")
    return result.stdout.strip()


def search_repos(org: str, query: str = "") -> list[dict]:
    """Search repositories in an organization."""
    args = ["repo", "list", org, "--json", "name,owner,description,url", "--limit", "100"]
    raw = _run(args)
    repos = json.loads(raw) if raw else []
    if query:
        q = query.lower()
        repos = [r for r in repos if q in r.get("name", "").lower() or q in (r.get("description") or "").lower()]
    return repos


def list_prs(repo_full_name: str) -> list[dict]:
    """List open PRs for a repository."""
    raw = _run([
        "pr", "list",
        "--repo", repo_full_name,
        "--json", "number,title,author,url,updatedAt,headRefName,baseRefName,state,additions,deletions",
        "--limit", "50",
        "--state", "open",
    ])
    return json.loads(raw) if raw else []


def get_pr_diff(repo_full_name: str, pr_number: int) -> str:
    """Get the diff for a PR."""
    return _run(["pr", "diff", str(pr_number), "--repo", repo_full_name])


def get_pr_comments(repo_full_name: str, pr_number: int) -> dict:
    """Get all comments for a PR: issue comments, review bodies, and inline review comments."""
    # Top-level PR data (issue comments + review bodies)
    raw = _run([
        "pr", "view", str(pr_number),
        "--repo", repo_full_name,
        "--json", "comments,reviews,reviewRequests,body,title,number",
    ])
    data = json.loads(raw) if raw else {}

    # Inline review comments (code-level) — these are NOT in `gh pr view --json`
    try:
        inline_raw = _run([
            "api", f"repos/{repo_full_name}/pulls/{pr_number}/comments",
            "--paginate",
        ])
        inline_comments = json.loads(inline_raw) if inline_raw else []
        # Normalize to same shape as issue comments
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
    except RuntimeError:
        data["review_comments"] = []

    return data


def post_comment(repo_full_name: str, pr_number: int, body: str) -> str:
    """Post a comment on a PR."""
    return _run([
        "pr", "comment", str(pr_number),
        "--repo", repo_full_name,
        "--body", body,
    ])
