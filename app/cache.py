"""JSON file cache management for repos, PRs, and reviews."""

import json
from pathlib import Path

CACHE_DIR = Path(__file__).parent.parent / ".cache"


def _ensure_dirs() -> None:
    (CACHE_DIR / "prs").mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / "reviews").mkdir(parents=True, exist_ok=True)


def _read(path: Path) -> dict | list | None:
    if path.exists():
        return json.loads(path.read_text())
    return None


def _write(path: Path, data: dict | list) -> None:
    _ensure_dirs()
    path.write_text(json.dumps(data, indent=2))


# --- Repos ---

def get_repos() -> list[dict]:
    return _read(CACHE_DIR / "repos.json") or []


def add_repo(owner: str, name: str) -> list[dict]:
    repos = get_repos()
    entry = {"owner": owner, "name": name, "full_name": f"{owner}/{name}"}
    if not any(r["full_name"] == entry["full_name"] for r in repos):
        repos.append(entry)
        _write(CACHE_DIR / "repos.json", repos)
    return repos


def remove_repo(full_name: str) -> list[dict]:
    repos = [r for r in get_repos() if r["full_name"] != full_name]
    _write(CACHE_DIR / "repos.json", repos)
    # Clean up related caches
    pr_file = CACHE_DIR / "prs" / f"{full_name.replace('/', '_')}.json"
    if pr_file.exists():
        pr_file.unlink()
    return repos


# --- PRs ---

def _pr_path(repo_full_name: str) -> Path:
    return CACHE_DIR / "prs" / f"{repo_full_name.replace('/', '_')}.json"


def get_prs(repo_full_name: str) -> list[dict]:
    return _read(_pr_path(repo_full_name)) or []


def save_prs(repo_full_name: str, prs: list[dict]) -> None:
    _write(_pr_path(repo_full_name), prs)


# --- Reviews ---

def _review_path(repo_full_name: str, pr_number: int) -> Path:
    return CACHE_DIR / "reviews" / f"{repo_full_name.replace('/', '_')}_{pr_number}.json"


def get_review(repo_full_name: str, pr_number: int) -> dict | None:
    return _read(_review_path(repo_full_name, pr_number))


def save_review(repo_full_name: str, pr_number: int, review: dict) -> None:
    _write(_review_path(repo_full_name, pr_number), review)
