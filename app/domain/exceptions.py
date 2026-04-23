"""Domain exceptions — one per failure category."""


class ProviderError(Exception):
    """AI provider call failed (timeout, parse error, subprocess failure)."""


class VCSError(Exception):
    """VCS operation failed (gh CLI, git, GitHub API)."""


class CacheError(Exception):
    """Cache read/write failed (JSON decode, file permission)."""


class WorktreeError(Exception):
    """Worktree operation failed (clone, checkout, push)."""


class WorktreeNotFoundError(WorktreeError):
    """Raised when the worktree directory does not exist."""


class WorktreeNoChangesError(WorktreeError):
    """Raised when the worktree has no uncommitted changes to commit."""
