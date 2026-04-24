"""GitHub CLI implementation of VCSPort."""
import json
import subprocess

from app.adapters._subprocess import SubprocessError, clean_env, run_subprocess
from app.domain.exceptions import VCSError
from app.domain.models import PR
from app.ports.vcs_port import VCSPort


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
            "--json", "number,title,author,url,updatedAt,headRefName,baseRefName,state,additions,deletions",
            "--limit", "50",
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
