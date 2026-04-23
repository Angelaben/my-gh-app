"""Git worktree implementation of WorktreePort."""
import logging
import os
import shutil

from app.adapters._subprocess import SubprocessError, run_git, run_subprocess
from app.domain.exceptions import WorktreeError
from app.ports.worktree_port import WorktreePort

logger = logging.getLogger(__name__)

CLONES_BASE = os.path.expanduser("~/.gh-review-tool/clones")
WORKTREES_BASE = os.path.expanduser("~/.gh-review-tool/worktrees")


def _slug(repo_full_name: str) -> str:
    return repo_full_name.replace("/", "_")


class GitWorktreeAdapter(WorktreePort):
    """Uses git worktrees backed by a bare clone per repository."""

    def worktree_path(self, repo_full_name: str, pr_number: int) -> str:
        return os.path.join(WORKTREES_BASE, f"{_slug(repo_full_name)}_pr{pr_number}")

    def _bare_path(self, repo_full_name: str) -> str:
        return os.path.join(CLONES_BASE, f"{_slug(repo_full_name)}.git")

    def _ensure_bare_clone(self, repo_full_name: str) -> str:
        os.makedirs(CLONES_BASE, exist_ok=True)
        bare_path = self._bare_path(repo_full_name)
        if os.path.isdir(bare_path):
            logger.info("Bare clone exists at %s, fetching...", bare_path)
            try:
                run_git(["fetch", "--all"], cwd=bare_path, timeout=120)
            except SubprocessError as e:
                raise WorktreeError(f"Failed to fetch: {e}") from e
            return bare_path
        logger.info("Creating bare clone of %s", repo_full_name)
        try:
            run_subprocess(
                ["gh", "repo", "clone", repo_full_name, bare_path, "--", "--bare"],
                timeout=300,
            )
        except SubprocessError as e:
            raise WorktreeError(f"Failed to clone {repo_full_name}: {e}") from e
        return bare_path

    def create(self, repo_full_name: str, pr_number: int, pr_branch: str) -> str:
        bare_path = self._ensure_bare_clone(repo_full_name)
        os.makedirs(WORKTREES_BASE, exist_ok=True)
        wt_path = self.worktree_path(repo_full_name, pr_number)

        if os.path.isdir(wt_path):
            logger.info("Worktree exists at %s, resetting...", wt_path)
            try:
                run_git(["fetch", "origin", pr_branch], cwd=wt_path, timeout=120)
                run_git(["reset", "--hard", "FETCH_HEAD"], cwd=wt_path)
                run_git(["clean", "-fd"], cwd=wt_path)
            except SubprocessError as e:
                raise WorktreeError(f"Failed to reset worktree: {e}") from e
            return wt_path

        try:
            run_git(["fetch", "origin", f"{pr_branch}:{pr_branch}"], cwd=bare_path, timeout=120)
            run_git(["worktree", "add", wt_path, pr_branch], cwd=bare_path)
        except SubprocessError as e:
            raise WorktreeError(f"Failed to create worktree: {e}") from e
        logger.info("Created worktree at %s on branch %s", wt_path, pr_branch)
        return wt_path

    def remove(self, repo_full_name: str, pr_number: int) -> None:
        bare_path = self._bare_path(repo_full_name)
        wt_path = self.worktree_path(repo_full_name, pr_number)

        if os.path.isdir(wt_path):
            shutil.rmtree(wt_path, ignore_errors=True)

        if os.path.isdir(bare_path):
            try:
                run_git(["worktree", "prune"], cwd=bare_path)
            except SubprocessError:
                pass

        logger.info("Removed worktree for PR #%s", pr_number)

    def list_worktrees(self) -> list[dict]:
        if not os.path.isdir(WORKTREES_BASE):
            return []
        return [
            {"name": entry, "path": os.path.join(WORKTREES_BASE, entry)}
            for entry in os.listdir(WORKTREES_BASE)
            if os.path.isdir(os.path.join(WORKTREES_BASE, entry))
        ]

    def has_changes(self, worktree_path: str) -> bool:
        try:
            result = run_git(["status", "--porcelain"], cwd=worktree_path)
            return bool(result.strip())
        except SubprocessError:
            return False

    def stage_all(self, worktree_path: str) -> None:
        try:
            run_git(["add", "-A"], cwd=worktree_path)
        except SubprocessError as e:
            raise WorktreeError(f"Failed to stage changes: {e}") from e

    def get_staged_diff(self, worktree_path: str) -> str:
        try:
            return run_git(["diff", "--cached"], cwd=worktree_path)
        except SubprocessError as e:
            raise WorktreeError(f"Failed to get diff: {e}") from e

    def commit_and_push(self, worktree_path: str, message: str) -> str:
        try:
            run_git(["commit", "-m", message], cwd=worktree_path, timeout=30)
            branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree_path)
            return run_git(["push", "-u", "origin", branch], cwd=worktree_path, timeout=120)
        except SubprocessError as e:
            raise WorktreeError(f"Failed to commit/push: {e}") from e

    def create_branch_and_push(
        self, worktree_path: str, new_branch: str, message: str
    ) -> str:
        try:
            run_git(["checkout", "-b", new_branch], cwd=worktree_path, timeout=10)
            run_git(["commit", "-m", message], cwd=worktree_path, timeout=30)
            return run_git(["push", "-u", "origin", new_branch], cwd=worktree_path, timeout=120)
        except SubprocessError as e:
            raise WorktreeError(f"Failed to create branch and push: {e}") from e
