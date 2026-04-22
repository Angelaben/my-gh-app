"""Wrapper around the gh CLI for GitHub interactions."""

import json
import logging
import os
import subprocess

logger = logging.getLogger(__name__)


def _clean_env() -> dict[str, str]:
    """Return a copy of the environment without GITHUB_TOKEN so gh uses keyring auth."""
    env = os.environ.copy()
    env.pop("GITHUB_TOKEN", None)
    env.pop("GH_TOKEN", None)
    return env


def _run(args: list[str], cwd: str | None = None, timeout: int = 60) -> str:
    result = subprocess.run(
        ["gh", *args], capture_output=True, text=True, timeout=timeout, env=_clean_env(), cwd=cwd
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh command failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _git(args: list[str], cwd: str, timeout: int = 120) -> str:
    """Run a git command in a specific directory."""
    env = _clean_env()
    result = subprocess.run(["git", *args], capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(f"git command failed: {result.stderr.strip()}")
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


# --- Clone / Branch ---


def clone_repo(repo_full_name: str, target_dir: str) -> str:
    """Clone a repository into target_dir. Returns the clone path."""
    _run(["repo", "clone", repo_full_name, target_dir], timeout=300)
    logger.info("Cloned %s into %s", repo_full_name, target_dir)
    return target_dir


def get_pr_head_branch(repo_full_name: str, pr_number: int) -> str:
    """Get the head branch name for a PR."""
    raw = _run(["pr", "view", str(pr_number), "--repo", repo_full_name, "--json", "headRefName"])
    return json.loads(raw)["headRefName"]


def checkout_pr_branch(repo_dir: str, pr_branch: str) -> None:
    """Checkout the PR's head branch in a cloned repo."""
    # Fetch the branch first in case it wasn't included in the clone
    _git(["fetch", "origin", pr_branch], cwd=repo_dir)
    _git(["checkout", pr_branch], cwd=repo_dir)
    logger.info("Checked out branch %s", pr_branch)


def has_changes(repo_dir: str) -> bool:
    """Check if there are uncommitted changes."""
    result = subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True, cwd=repo_dir, env=_clean_env()
    )
    return bool(result.stdout.strip())


def get_diff(repo_dir: str) -> str:
    """Get the full diff of uncommitted changes (staged + unstaged)."""
    # Stage everything first so diff shows all changes
    _git(["add", "-A"], cwd=repo_dir)
    return _git(["diff", "--cached"], cwd=repo_dir)


def delete_remote_branch(repo_full_name: str, branch_name: str) -> None:
    """Delete a remote branch."""
    _run(["api", f"repos/{repo_full_name}/git/refs/heads/{branch_name}", "--method", "DELETE"])
