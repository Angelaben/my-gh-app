"""Pure domain models. No framework dependencies."""
from dataclasses import dataclass


@dataclass
class Finding:
    priority: str  # P0 | P1 | P2 | P3
    title: str
    description: str
    file: str | None = None
    line: int | None = None
    suggestion: str | None = None


@dataclass
class Review:
    summary: str
    findings: list[Finding]
    raw_output: str | None = None
    raw_length: int | None = None


@dataclass
class Repo:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass
class PR:
    number: int
    title: str
    author: str
    branch: str
    base_branch: str
    additions: int
    deletions: int
    updated_at: str
    url: str


@dataclass
class Comment:
    id: int
    author: str
    body: str
    file: str | None = None
    line: int | None = None
    created_at: str | None = None
    in_reply_to_id: int | None = None   # GitHub threading — inline review comments only
    thread_id: int | None = None         # root comment ID of this thread (computed)
    is_ours: bool = False                # author == authenticated GitHub login
    is_new_reply: bool = False           # reply to our comment, created after last visit
    has_new_replies: bool = False        # set on root comment when thread has new replies
    comment_type: str = 'pr_comment'    # 'pr_comment' | 'review_comment' — determines delete API path


@dataclass
class FixResult:
    worktree_path: str
    branch: str
    has_changes: bool
    diff: str
    output: str
