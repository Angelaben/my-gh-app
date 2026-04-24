"""JSON-on-disk implementation of CachePort."""
import json
from datetime import datetime, timezone
from pathlib import Path

from app.domain.exceptions import CacheError
from app.domain.models import Finding, Review
from app.ports.cache_port import CachePort


def _slug(repo_full_name: str) -> str:
    return repo_full_name.replace("/", "_")


class JsonFileCache(CachePort):
    """Stores repos, PRs, and reviews as JSON files under cache_dir."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        if cache_dir is None:
            cache_dir = Path(__file__).parent.parent.parent.parent / ".cache"
        self._dir = cache_dir
        self._prs_dir = self._dir / "prs"
        self._reviews_dir = self._dir / "reviews"
        self._visits_dir = self._dir / "visits"

    def _ensure_dirs(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._prs_dir.mkdir(parents=True, exist_ok=True)
        self._reviews_dir.mkdir(parents=True, exist_ok=True)
        self._visits_dir.mkdir(parents=True, exist_ok=True)

    def _read_json(self, path: Path) -> dict | list | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError as e:
            raise CacheError(f"Failed to parse {path}: {e}") from e

    def _write_json(self, path: Path, data: dict | list) -> None:
        self._ensure_dirs()
        path.write_text(json.dumps(data, indent=2))

    # --- Repos ---

    def get_repos(self) -> list[dict]:
        return self._read_json(self._dir / "repos.json") or []

    def add_repo(self, owner: str, name: str) -> list[dict]:
        repos = self.get_repos()
        entry = {"owner": owner, "name": name, "full_name": f"{owner}/{name}"}
        if not any(r["full_name"] == entry["full_name"] for r in repos):
            repos.append(entry)
            self._write_json(self._dir / "repos.json", repos)
        return repos

    def remove_repo(self, full_name: str) -> list[dict]:
        repos = [r for r in self.get_repos() if r["full_name"] != full_name]
        self._write_json(self._dir / "repos.json", repos)
        pr_file = self._prs_dir / f"{_slug(full_name)}.json"
        if pr_file.exists():
            pr_file.unlink()
        return repos

    # --- PRs ---

    def get_prs(self, repo_full_name: str) -> list[dict]:
        path = self._prs_dir / f"{_slug(repo_full_name)}.json"
        return self._read_json(path) or []

    def save_prs(self, repo_full_name: str, prs: list[dict]) -> None:
        self._write_json(self._prs_dir / f"{_slug(repo_full_name)}.json", prs)

    # --- Reviews ---

    def _review_path(self, repo_full_name: str, pr_number: int) -> Path:
        return self._reviews_dir / f"{_slug(repo_full_name)}_{pr_number}.json"

    def get_review(self, repo_full_name: str, pr_number: int) -> Review | None:
        data = self._read_json(self._review_path(repo_full_name, pr_number))
        if data is None:
            return None
        findings = [
            Finding(
                priority=f.get("priority", f.get("criticality", "P3")),
                title=f.get("title", ""),
                description=f.get("description", ""),
                file=f.get("file"),
                line=f.get("line"),
                suggestion=f.get("suggestion"),
            )
            for f in data.get("findings", [])
        ]
        return Review(
            summary=data.get("summary", ""),
            findings=findings,
            raw_output=data.get("raw_output"),
            raw_length=data.get("raw_length"),
        )

    def save_review(self, repo_full_name: str, pr_number: int, review: Review) -> None:
        data: dict = {
            "summary": review.summary,
            "findings": [
                {
                    "priority": f.priority,
                    "title": f.title,
                    "description": f.description,
                    "file": f.file,
                    "line": f.line,
                    "suggestion": f.suggestion,
                }
                for f in review.findings
            ],
        }
        if review.raw_output is not None:
            data["raw_output"] = review.raw_output
        if review.raw_length is not None:
            data["raw_length"] = review.raw_length
        self._write_json(self._review_path(repo_full_name, pr_number), data)

    def clear_review(self, repo_full_name: str, pr_number: int) -> None:
        path = self._review_path(repo_full_name, pr_number)
        if path.exists():
            path.unlink()

    # --- Last Visited & GitHub Login ---

    def get_last_visited(self, repo_full_name: str, pr_number: int) -> datetime | None:
        path = self._visits_dir / f"{_slug(repo_full_name)}_{pr_number}.json"
        data = self._read_json(path)
        if data is None or "last_visited_at" not in data:
            return None
        try:
            return datetime.fromisoformat(data["last_visited_at"])
        except ValueError:
            return None

    def set_last_visited(self, repo_full_name: str, pr_number: int, dt: datetime) -> None:
        path = self._visits_dir / f"{_slug(repo_full_name)}_{pr_number}.json"
        self._write_json(path, {"last_visited_at": dt.isoformat()})

    def get_github_login(self) -> str | None:
        data = self._read_json(self._dir / "github_login.json")
        return data.get("login") if data else None

    def set_github_login(self, login: str) -> None:
        self._write_json(self._dir / "github_login.json", {"login": login})
