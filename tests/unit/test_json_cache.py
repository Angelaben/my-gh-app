"""Tests for the JSON file cache adapter."""
import pytest
from pathlib import Path
from app.adapters.cache.json_file_cache import JsonFileCache
from app.domain.models import Finding, Review


@pytest.fixture
def cache(tmp_path: Path) -> JsonFileCache:
    return JsonFileCache(cache_dir=tmp_path)


class TestRepos:
    def test_empty_by_default(self, cache: JsonFileCache):
        assert cache.get_repos() == []

    def test_add_repo(self, cache: JsonFileCache):
        result = cache.add_repo("acme", "backend")
        assert len(result) == 1
        assert result[0]["full_name"] == "acme/backend"
        assert result[0]["owner"] == "acme"
        assert result[0]["name"] == "backend"

    def test_add_repo_idempotent(self, cache: JsonFileCache):
        cache.add_repo("acme", "backend")
        result = cache.add_repo("acme", "backend")
        assert len(result) == 1

    def test_remove_repo(self, cache: JsonFileCache):
        cache.add_repo("acme", "backend")
        result = cache.remove_repo("acme/backend")
        assert result == []

    def test_remove_repo_cleans_pr_cache(self, cache: JsonFileCache):
        cache.add_repo("acme", "backend")
        cache.save_prs("acme/backend", [{"number": 1}])
        cache.remove_repo("acme/backend")
        assert cache.get_prs("acme/backend") == []


class TestPRs:
    def test_empty_by_default(self, cache: JsonFileCache):
        assert cache.get_prs("acme/backend") == []

    def test_save_and_retrieve(self, cache: JsonFileCache):
        prs = [{"number": 1, "title": "fix bug"}]
        cache.save_prs("acme/backend", prs)
        assert cache.get_prs("acme/backend") == prs


class TestReview:
    def test_none_when_missing(self, cache: JsonFileCache):
        assert cache.get_review("acme/backend", 42) is None

    def test_save_and_retrieve(self, cache: JsonFileCache):
        review = Review(
            summary="LGTM",
            findings=[Finding(priority="P2", title="Style", description="Use f-string")],
        )
        cache.save_review("acme/backend", 42, review)
        loaded = cache.get_review("acme/backend", 42)
        assert loaded is not None
        assert loaded.summary == "LGTM"
        assert len(loaded.findings) == 1
        assert loaded.findings[0].priority == "P2"

    def test_clear_review(self, cache: JsonFileCache):
        review = Review(summary="ok", findings=[])
        cache.save_review("acme/backend", 1, review)
        cache.clear_review("acme/backend", 1)
        assert cache.get_review("acme/backend", 1) is None

    def test_clear_nonexistent_is_noop(self, cache: JsonFileCache):
        cache.clear_review("acme/backend", 999)  # Should not raise

    def test_corrupt_json_raises_cache_error(self, cache: JsonFileCache, tmp_path: Path):
        from app.domain.exceptions import CacheError
        # Write a malformed JSON file directly
        pr_file = tmp_path / "prs" / "acme_backend.json"
        pr_file.parent.mkdir(parents=True, exist_ok=True)
        pr_file.write_text("not valid json {{{")
        with pytest.raises(CacheError):
            cache.get_prs("acme/backend")

    def test_finding_line_roundtrip_preserves_int(self, cache: JsonFileCache):
        review = Review(
            summary="test",
            findings=[Finding(priority="P1", title="t", description="d", line=42)],
        )
        cache.save_review("acme/backend", 1, review)
        loaded = cache.get_review("acme/backend", 1)
        assert loaded is not None
        assert loaded.findings[0].line == 42
        assert isinstance(loaded.findings[0].line, int)
