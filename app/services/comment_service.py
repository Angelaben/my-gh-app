"""CommentService — aggregates and analyzes PR comments."""
from dataclasses import replace
from datetime import datetime

from app.domain.models import Comment
from app.ports.ai_provider import AIProvider
from app.ports.vcs_port import VCSPort


def _normalize_author(author: str | dict) -> str:
    if isinstance(author, dict):
        return author.get("login", "unknown")
    return author or "unknown"


def _parse_dt(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))


def build_threads(comments: list[Comment]) -> dict[int, list[Comment]]:
    """Return {root_id: [root, reply1, reply2, ...]} for all comments."""
    by_id: dict[int, Comment] = {c.id: c for c in comments if c.id}

    def get_root_id(c: Comment) -> int:
        visited: set[int] = set()
        while c.in_reply_to_id is not None and c.in_reply_to_id in by_id:
            if c.id in visited:
                break  # cycle detected
            visited.add(c.id)
            c = by_id[c.in_reply_to_id]
        return c.id

    threads: dict[int, list[Comment]] = {}
    for c in comments:
        threads.setdefault(get_root_id(c), []).append(c)
    return threads


def enrich_comments(
    comments: list[Comment],
    our_login: str,
    last_visited_at: datetime | None,
) -> tuple[list[Comment], list[int]]:
    """
    Enrich comments with threading metadata and visit-aware flags.

    Returns (enriched_comments, new_comment_ids) where new_comment_ids
    contains IDs of non-ours comments created after last_visited_at.
    """
    threads = build_threads(comments)
    is_ours_map = {c.id: _normalize_author(c.author) == our_login for c in comments}
    new_comment_ids: list[int] = []
    result: list[Comment] = []

    for root_id, thread_comments in threads.items():
        root_is_ours = is_ours_map.get(root_id, False)

        new_replies = [
            tc for tc in thread_comments
            if tc.id != root_id
            and not is_ours_map.get(tc.id, True)
            and last_visited_at is not None
            and tc.created_at is not None
            and _parse_dt(tc.created_at) > last_visited_at
        ]
        has_new_replies = root_is_ours and len(new_replies) > 0

        for tc in thread_comments:
            is_ours = is_ours_map.get(tc.id, False)
            is_new = (
                last_visited_at is not None
                and tc.created_at is not None
                and _parse_dt(tc.created_at) > last_visited_at
                and not is_ours
            )
            if is_new and tc.id:
                new_comment_ids.append(tc.id)

            is_new_reply = (
                root_is_ours
                and tc.id != root_id
                and not is_ours
                and is_new
            )

            result.append(replace(
                tc,
                thread_id=root_id,
                is_ours=is_ours,
                is_new_reply=is_new_reply,
                has_new_replies=has_new_replies if tc.id == root_id else False,
            ))

    return result, new_comment_ids


def _dict_to_comment(d: dict) -> Comment:
    return Comment(
        id=d.get("id") or 0,
        author=d.get("author", ""),
        body=d.get("body", ""),
        file=d.get("path") or d.get("file"),
        line=d.get("line"),
        created_at=d.get("created_at"),
        in_reply_to_id=d.get("in_reply_to_id"),
        comment_type=d.get("comment_type", "pr_comment"),
    )


class CommentService:
    def __init__(self, ai: AIProvider, vcs: VCSPort) -> None:
        self._ai = ai
        self._vcs = vcs

    def _collect_comments(self, data: dict) -> list[dict]:
        """Aggregate PR comments, reviews, and inline review comments into a flat list."""
        result: list[dict] = []
        synthetic_id = -1  # Negative IDs for items without real GitHub IDs

        for c in data.get("comments", []):
            real_id = c.get("databaseId") or c.get("id")
            comment_id = real_id if real_id else synthetic_id
            if not real_id:
                synthetic_id -= 1
            result.append(c | {
                "author": _normalize_author(c.get("author", "")),
                "id": comment_id,
                "in_reply_to_id": None,
            })

        for review in data.get("reviews", []):
            if review.get("body"):
                real_id = review.get("databaseId") or review.get("id")
                review_id = real_id if real_id else synthetic_id
                if not real_id:
                    synthetic_id -= 1
                result.append(review | {
                    "author": _normalize_author(review.get("author", "")),
                    "id": review_id,
                    "in_reply_to_id": None,
                    "comment_type": "pr_comment",
                })

        for rc in data.get("review_comments", []):
            result.append(rc | {
                "author": _normalize_author(rc.get("author", "")),
                "in_reply_to_id": rc.get("in_reply_to_id"),
                "comment_type": "review_comment",
                "_inline": True,
            })

        return result

    def get_comments(self, repo_full_name: str, pr_number: int) -> dict:
        """Fetch and normalize all comments for a PR.

        Returns: {"comments": list[dict]} where each comment has author (str),
        body, and _inline: True for inline review comments.
        """
        data = self._vcs.get_comments(repo_full_name, pr_number)
        return {"comments": self._collect_comments(data)}

    def get_enriched_comments(
        self,
        repo_full_name: str,
        pr_number: int,
        our_login: str,
        last_visited_at: "datetime | None",
    ) -> "tuple[list[Comment], list[int]]":
        """Fetch, aggregate, and enrich all PR comments.

        Returns (enriched_comments, new_comment_ids).
        """
        data = self._vcs.get_comments(repo_full_name, pr_number)
        raw = self._collect_comments(data)
        comment_objs = [_dict_to_comment(d) for d in raw]
        return enrich_comments(comment_objs, our_login, last_visited_at)

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
