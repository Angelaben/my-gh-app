"""CommentService — aggregates and analyzes PR comments."""
from app.domain.models import Comment
from app.ports.ai_provider import AIProvider
from app.ports.vcs_port import VCSPort


def _normalize_author(author: str | dict) -> str:
    if isinstance(author, dict):
        return author.get("login", "unknown")
    return author or "unknown"


class CommentService:
    def __init__(self, ai: AIProvider, vcs: VCSPort) -> None:
        self._ai = ai
        self._vcs = vcs

    def _collect_comments(self, data: dict) -> list[dict]:
        """Aggregate and normalize comments, reviews, and inline review comments."""
        result: list[dict] = []
        for c in data.get("comments", []):
            result.append(c | {"author": _normalize_author(c.get("author", ""))})
        for review in data.get("reviews", []):
            if review.get("body"):
                result.append(review | {"author": _normalize_author(review.get("author", ""))})
        for rc in data.get("review_comments", []):
            result.append(rc | {"author": _normalize_author(rc.get("author", "")), "_inline": True})
        return result

    def get_comments(self, repo_full_name: str, pr_number: int) -> dict:
        """Fetch and normalize all comments for a PR.

        Returns: {"comments": list[dict]} where each comment has author (str),
        body, and _inline: True for inline review comments.
        """
        data = self._vcs.get_comments(repo_full_name, pr_number)
        return {"comments": self._collect_comments(data)}

    async def analyze_comments(self, repo_full_name: str, pr_number: int) -> dict:
        """Analyze all PR comments using the AI provider.

        Returns: {"comments": list[dict], "analysis": list[dict]}
        """
        data = self._vcs.get_comments(repo_full_name, pr_number)
        all_comments = self._collect_comments(data)
        domain_comments = [
            Comment(
                id=i,
                author=c["author"],
                body=c.get("body", ""),
                file=c.get("path") or c.get("file"),
                line=c.get("line"),
                created_at=c.get("created_at"),
            )
            for i, c in enumerate(all_comments)
        ]
        analysis = await self._ai.analyze_comments(repo_full_name, pr_number, domain_comments)
        return {"comments": all_comments, "analysis": analysis}

    def post_comment(self, repo_full_name: str, pr_number: int, body: str) -> str:
        return self._vcs.post_comment(repo_full_name, pr_number, body)

    def post_inline_comment(
        self,
        repo_full_name: str,
        pr_number: int,
        body: str,
        path: str,
        line: int,
    ) -> dict:
        return self._vcs.post_inline_comment(repo_full_name, pr_number, body, path, line)
