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


@dataclass
class FixResult:
    worktree_path: str
    branch: str
    has_changes: bool
    diff: str
    output: str
