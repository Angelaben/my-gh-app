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


def get_pr_head_sha(repo_full_name: str, pr_number: int) -> str:
    """Get the HEAD commit SHA of a PR."""
    raw = _run([
        "pr", "view", str(pr_number),
        "--repo", repo_full_name,
        "--json", "headRefOid",
    ])
    return json.loads(raw)["headRefOid"]


def post_inline_comment(
    repo_full_name: str,
    pr_number: int,
    body: str,
    path: str,
    line: int,
    commit_id: str | None = None,
) -> dict:
    """Post an inline review comment on a specific file/line in a PR diff."""
    if commit_id is None:
        commit_id = get_pr_head_sha(repo_full_name, pr_number)

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
        input=payload, capture_output=True, text=True, timeout=30, env=_clean_env(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to post inline comment: {result.stderr.strip()}")
    return json.loads(result.stdout)


# --- Clone / Worktree ---

CLONES_BASE = os.path.expanduser("~/.gh-review-tool/clones")
WORKTREES_BASE = os.path.expanduser("~/.gh-review-tool/worktrees")


def _ensure_bare_clone(repo_full_name: str) -> str:
    """Ensure a bare clone exists for the repo. Returns the bare clone path."""
    os.makedirs(CLONES_BASE, exist_ok=True)
    repo_slug = repo_full_name.replace("/", "_")
    bare_path = os.path.join(CLONES_BASE, f"{repo_slug}.git")

    if os.path.isdir(bare_path):
        # Already cloned — just fetch latest
        logger.info("Bare clone exists at %s, fetching...", bare_path)
        _git(["fetch", "--all"], cwd=bare_path, timeout=120)
        return bare_path

    # Clone as bare
    logger.info("Creating bare clone of %s", repo_full_name)
    _run(["repo", "clone", repo_full_name, bare_path, "--", "--bare"], timeout=300)
    return bare_path


def get_pr_head_branch(repo_full_name: str, pr_number: int) -> str:
    """Get the head branch name for a PR."""
    raw = _run(["pr", "view", str(pr_number), "--repo", repo_full_name, "--json", "headRefName"])
    return json.loads(raw)["headRefName"]


def create_worktree(repo_full_name: str, pr_number: int, pr_branch: str) -> str:
    """Create a git worktree for a specific PR branch. Returns the worktree path.
    If the worktree already exists, returns the existing path."""
    bare_path = _ensure_bare_clone(repo_full_name)

    os.makedirs(WORKTREES_BASE, exist_ok=True)
    repo_slug = repo_full_name.replace("/", "_")
    worktree_path = os.path.join(WORKTREES_BASE, f"{repo_slug}_pr{pr_number}")

    if os.path.isdir(worktree_path):
        logger.info("Worktree already exists at %s, resetting...", worktree_path)
        # Fetch latest into the worktree directly and reset
        _git(["fetch", "origin", pr_branch], cwd=worktree_path, timeout=120)
        _git(["reset", "--hard", "FETCH_HEAD"], cwd=worktree_path)
        _git(["clean", "-fd"], cwd=worktree_path)
        return worktree_path

    # Fetch the branch in the bare clone
    _git(["fetch", "origin", f"{pr_branch}:{pr_branch}"], cwd=bare_path, timeout=120)

    # Create worktree
    _git(["worktree", "add", worktree_path, pr_branch], cwd=bare_path)
    logger.info("Created worktree at %s on branch %s", worktree_path, pr_branch)
    return worktree_path


def remove_worktree(repo_full_name: str, pr_number: int) -> None:
    """Remove a worktree for a PR."""
    repo_slug = repo_full_name.replace("/", "_")
    bare_path = os.path.join(CLONES_BASE, f"{repo_slug}.git")
    worktree_path = os.path.join(WORKTREES_BASE, f"{repo_slug}_pr{pr_number}")

    if os.path.isdir(worktree_path):
        import shutil
        shutil.rmtree(worktree_path, ignore_errors=True)

    if os.path.isdir(bare_path):
        # Prune stale worktree references
        try:
            _git(["worktree", "prune"], cwd=bare_path)
        except RuntimeError:
            pass

    logger.info("Removed worktree for PR #%s", pr_number)


def list_worktrees() -> list[dict]:
    """List all existing worktrees."""
    if not os.path.isdir(WORKTREES_BASE):
        return []
    result = []
    for entry in os.listdir(WORKTREES_BASE):
        full = os.path.join(WORKTREES_BASE, entry)
        if os.path.isdir(full):
            result.append({"name": entry, "path": full})
    return result


def has_changes(repo_dir: str) -> bool:
    """Check if there are uncommitted changes."""
    result = subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True, cwd=repo_dir, env=_clean_env()
    )
    return bool(result.stdout.strip())


def get_diff(repo_dir: str) -> str:
    """Get the full diff of uncommitted changes (staged + unstaged)."""
    _git(["add", "-A"], cwd=repo_dir)
    return _git(["diff", "--cached"], cwd=repo_dir)


def commit_and_push(repo_dir: str, message: str) -> str:
    """Commit all staged changes and push to origin. Returns the push output."""
    _git(["add", "-A"], cwd=repo_dir)
    _git(["commit", "-m", message], cwd=repo_dir, timeout=30)
    return _git(["push"], cwd=repo_dir, timeout=120)


def create_branch_and_push(repo_dir: str, new_branch: str, message: str) -> str:
    """Create a new branch from current state, commit, and push."""
    _git(["checkout", "-b", new_branch], cwd=repo_dir, timeout=10)
    _git(["add", "-A"], cwd=repo_dir)
    _git(["commit", "-m", message], cwd=repo_dir, timeout=30)
    return _git(["push", "-u", "origin", new_branch], cwd=repo_dir, timeout=120)


def create_pr(repo_full_name: str, head_branch: str, base_branch: str, title: str, body: str) -> dict:
    """Create a new PR using gh CLI. Returns the PR data."""
    raw = _run([
        "pr", "create",
        "--repo", repo_full_name,
        "--head", head_branch,
        "--base", base_branch,
        "--title", title,
        "--body", body,
    ], timeout=30)
    # gh pr create returns the PR URL
    return {"url": raw.strip()}
