"""GitHub CLI implementation of VCSPort."""
import json
import logging
import os
import re
import subprocess
import time

from app.adapters._subprocess import SubprocessError, clean_env, run_subprocess
from app.domain.exceptions import VCSError
from app.domain.models import PR
from app.ports.vcs_port import VCSPort

logger = logging.getLogger(__name__)

# Transient HTTP statuses where retrying with backoff is safe and effective.
# 408 = Request Timeout, 425 = Too Early, 429 = Rate Limited (best-effort),
# 500/502/503/504 = upstream/gateway hiccups.
_RETRYABLE_HTTP = re.compile(r"\bHTTP (408|425|429|500|502|503|504)\b")
_RETRY_DELAYS_S: tuple[float, ...] = (1.0, 2.0, 4.0)

_DEFAULT_PR_LIST_LIMIT = 50


def _pr_list_limit() -> int:
    raw = os.getenv("PR_LIST_LIMIT", "")
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_PR_LIST_LIMIT
    return value if value > 0 else _DEFAULT_PR_LIST_LIMIT


class GitHubCLIAdapter(VCSPort):
    """Implements VCSPort using the `gh` CLI tool."""

    def _run(self, args: list[str], *, cwd: str | None = None, timeout: int = 60) -> str:
        try:
            return run_subprocess(["gh", *args], cwd=cwd, timeout=timeout)
        except SubprocessError as e:
            raise VCSError(f"gh command failed: {e}") from e

    def search_repos(self, org: str, query: str = "") -> list[dict]:
        args = ["repo", "list", org, "--json", "name,owner,description,url", "--limit", "100"]
        raw = self._run(args)
        repos = json.loads(raw) if raw else []
        if query:
            q = query.lower()
            repos = [
                r for r in repos
                if q in r.get("name", "").lower() or q in (r.get("description") or "").lower()
            ]
        return repos

    def list_prs(self, repo_full_name: str) -> list[PR]:
        raw = self._run([
            "pr", "list",
            "--repo", repo_full_name,
            "--json", "number,title,author,url,updatedAt,headRefName,baseRefName,isDraft,additions,deletions",
            "--limit", str(_pr_list_limit()),
            "--state", "open",
        ])
        prs = json.loads(raw) if raw else []
        return [
            PR(
                number=pr["number"],
                title=pr["title"],
                author=(
                    pr.get("author", {}).get("login", "unknown")
                    if isinstance(pr.get("author"), dict)
                    else pr.get("author", "unknown")
                ),
                branch=pr["headRefName"],
                base_branch=pr["baseRefName"],
                additions=pr.get("additions", 0),
                deletions=pr.get("deletions", 0),
                updated_at=pr["updatedAt"],
                url=pr["url"],
                is_draft=pr.get("isDraft", False),
            )
            for pr in prs
        ]

    def get_diff(self, repo_full_name: str, pr_number: int) -> str:
        try:
            return self._run(["pr", "diff", str(pr_number), "--repo", repo_full_name])
        except VCSError:
            # Fallback for merged/closed PRs where gh pr diff fails:
            # fetch the raw diff via the GitHub API with the diff media type.
            return self._run([
                "api", f"repos/{repo_full_name}/pulls/{pr_number}",
                "--header", "Accept: application/vnd.github.diff",
            ])

    def get_comments(self, repo_full_name: str, pr_number: int) -> dict:
        raw = self._run([
            "pr", "view", str(pr_number),
            "--repo", repo_full_name,
            "--json", "comments,reviews,reviewRequests,body,title,number",
        ])
        data = json.loads(raw) if raw else {}

        try:
            inline_raw = self._run([
                "api", f"repos/{repo_full_name}/pulls/{pr_number}/comments",
                "--paginate",
            ])
            inline_comments = json.loads(inline_raw) if inline_raw else []
            data["review_comments"] = [
                {
                    "id": c.get("id"),
                    "author": {"login": c.get("user", {}).get("login", "unknown")},
                    "body": c.get("body", ""),
                    "path": c.get("path", ""),
                    "line": c.get("line"),
                    "created_at": c.get("created_at", ""),
                    "in_reply_to_id": c.get("in_reply_to_id"),
                }
                for c in inline_comments
            ]
        except VCSError:
            data["review_comments"] = []

        return data

    def post_comment(self, repo_full_name: str, pr_number: int, body: str) -> str:
        return self._run([
            "pr", "comment", str(pr_number),
            "--repo", repo_full_name,
            "--body", body,
        ])

    def get_pr_head_sha(self, repo_full_name: str, pr_number: int) -> str:
        raw = self._run([
            "pr", "view", str(pr_number),
            "--repo", repo_full_name,
            "--json", "headRefOid",
        ])
        return json.loads(raw)["headRefOid"]

    def post_inline_comment(
        self,
        repo_full_name: str,
        pr_number: int,
        body: str,
        path: str,
        line: int,
        commit_id: str | None = None,
    ) -> dict:
        if commit_id is None:
            commit_id = self.get_pr_head_sha(repo_full_name, pr_number)

        payload = json.dumps({
            "body": body,
            "commit_id": commit_id,
            "path": path,
            "line": line,
            "side": "RIGHT",
        })

        try:
            result = subprocess.run(
                ["gh", "api", f"repos/{repo_full_name}/pulls/{pr_number}/comments",
                 "--method", "POST", "--input", "-"],
                input=payload, capture_output=True, text=True, timeout=30, env=clean_env(),
            )
        except subprocess.TimeoutExpired as e:
            raise VCSError("post_inline_comment timed out after 30s") from e

        if result.returncode != 0:
            raise VCSError(f"Failed to post inline comment: {result.stderr.strip()}")
        return json.loads(result.stdout)

    def get_pr_head_branch(self, repo_full_name: str, pr_number: int) -> str:
        raw = self._run([
            "pr", "view", str(pr_number),
            "--repo", repo_full_name,
            "--json", "headRefName",
        ])
        return json.loads(raw)["headRefName"]

    def create_pr(
        self,
        repo_full_name: str,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> dict:
        raw = self._run([
            "pr", "create",
            "--repo", repo_full_name,
            "--head", head_branch,
            "--base", base_branch,
            "--title", title,
            "--body", body,
            "--json", "url",
        ], timeout=30)
        return json.loads(raw)

    def get_authenticated_user(self) -> str:
        raw = self._run(["api", "user", "--jq", ".login"])
        return raw.strip()

    def delete_comment(self, repo_full_name: str, comment_id: int, comment_type: str) -> None:
        if comment_type == "review_comment":
            path = f"repos/{repo_full_name}/pulls/comments/{comment_id}"
        else:
            path = f"repos/{repo_full_name}/issues/comments/{comment_id}"
        self._run(["api", path, "--method", "DELETE"])

    def create_review(
        self,
        repo_full_name: str,
        pr_number: int,
        body: str,
        event: str,
        comments: list[dict],
        commit_id: str | None = None,
    ) -> dict:
        if event not in ("APPROVE", "REQUEST_CHANGES", "COMMENT"):
            raise VCSError(f"invalid review event {event!r}")
        if commit_id is None:
            commit_id = self.get_pr_head_sha(repo_full_name, pr_number)

        result = self._post_review_with_retry(
            repo_full_name, pr_number, body, event, comments, commit_id,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
        logger.warning(
            "create_review | initial attempt failed | repo=%s pr=#%d stderr=%s",
            repo_full_name, pr_number, result.stderr.strip(),
        )

        # 422 most often means at least one inline comment references a line
        # that isn't in the PR diff. The reviews endpoint is all-or-nothing,
        # so we fold the findings into the body and retry without comments.
        body_for_fallback = body
        last_stderr = result.stderr
        if "422" in result.stderr and comments:
            body_for_fallback = self._fold_comments_into_body(body, comments)
            retry = self._post_review_with_retry(
                repo_full_name, pr_number, body_for_fallback, event, [], commit_id,
            )
            if retry.returncode == 0:
                parsed = json.loads(retry.stdout)
                parsed["_fallback_applied"] = "comments_folded_into_body"
                return parsed
            logger.warning(
                "create_review | fold-comments fallback failed | repo=%s pr=#%d stderr=%s",
                repo_full_name, pr_number, retry.stderr.strip(),
            )
            last_stderr = retry.stderr

        # Persistent 422: GitHub is rejecting the review itself, not the
        # inline comments. Common causes: reviewing your own PR (you can
        # only COMMENT, not REQUEST_CHANGES on a PR you authored), or the
        # PR is closed. Fall back to a regular issue comment with the body
        # so the user's findings still land on the PR — but mark the result
        # so the caller can warn the user that the review state was lost.
        if "422" in last_stderr:
            comment_url = self.post_comment(repo_full_name, pr_number, body_for_fallback)
            return {
                "_fallback_applied": "posted_as_comment",
                "_fallback_reason": last_stderr.strip(),
                "html_url": comment_url,
                "id": None,
            }

        raise VCSError(f"Failed to create review: {last_stderr.strip()}")

    def _post_review_with_retry(
        self,
        repo_full_name: str,
        pr_number: int,
        body: str,
        event: str,
        comments: list[dict],
        commit_id: str,
    ) -> "subprocess.CompletedProcess[str]":
        """Retry transient 5xx / rate-limit errors from the reviews endpoint.

        Returns the final ``CompletedProcess``; the caller decides what to do
        with non-zero exits that aren't transient (e.g. 422 → fold-and-retry).
        """
        last = self._post_review_payload(
            repo_full_name, pr_number, body, event, comments, commit_id,
        )
        for delay in _RETRY_DELAYS_S:
            if last.returncode == 0 or not _RETRYABLE_HTTP.search(last.stderr):
                return last
            time.sleep(delay)
            last = self._post_review_payload(
                repo_full_name, pr_number, body, event, comments, commit_id,
            )
        return last

    def _post_review_payload(
        self,
        repo_full_name: str,
        pr_number: int,
        body: str,
        event: str,
        comments: list[dict],
        commit_id: str,
    ) -> "subprocess.CompletedProcess[str]":
        payload = json.dumps({
            "commit_id": commit_id,
            "body": body,
            "event": event,
            "comments": [
                {"path": c["path"], "line": c["line"], "body": c["body"], "side": "RIGHT"}
                for c in comments
            ],
        })
        try:
            return subprocess.run(
                ["gh", "api", f"repos/{repo_full_name}/pulls/{pr_number}/reviews",
                 "--method", "POST", "--input", "-"],
                input=payload, capture_output=True, text=True, timeout=60, env=clean_env(),
            )
        except subprocess.TimeoutExpired as e:
            raise VCSError("create_review timed out after 60s") from e

    @staticmethod
    def _fold_comments_into_body(body: str, comments: list[dict]) -> str:
        """Append inline comments into the review body when GitHub rejects them.

        Used as a fallback when the reviews API returns 422 — typically because
        a line is outside the PR diff. Keeps each comment readable and pointed
        at its file/line.
        """
        lines: list[str] = [body, "", "**Findings:**", ""]
        for c in comments:
            lines.append(f"- `{c['path']}:{c['line']}`")
            for body_line in str(c["body"]).splitlines():
                lines.append(f"  {body_line}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
