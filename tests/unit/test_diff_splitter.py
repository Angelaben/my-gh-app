"""Tests for app.services._diff_splitter."""
from app.services._diff_splitter import (
    DEFAULT_IGNORE_GLOBS,
    FileDiff,
    filter_ignored_files,
    pack_chunks,
    split_unified_diff,
)


SAMPLE_DIFF = """diff --git a/foo.py b/foo.py
index 1111..2222 100644
--- a/foo.py
+++ b/foo.py
@@ -1,2 +1,3 @@
 line one
+added line
 line two
diff --git a/bar/baz.py b/bar/baz.py
index 3333..4444 100644
--- a/bar/baz.py
+++ b/bar/baz.py
@@ -10,1 +10,1 @@
-old
+new
diff --git a/README.md b/README.md
index 5555..6666 100644
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-Hello
+Hello world
"""


class TestSplitUnifiedDiff:
    def test_splits_three_files(self):
        files = split_unified_diff(SAMPLE_DIFF)
        assert len(files) == 3
        assert [f.path for f in files] == ["foo.py", "bar/baz.py", "README.md"]

    def test_each_section_starts_with_diff_git(self):
        files = split_unified_diff(SAMPLE_DIFF)
        for f in files:
            assert f.content.startswith("diff --git ")

    def test_concatenated_sections_equal_original(self):
        files = split_unified_diff(SAMPLE_DIFF)
        assert "".join(f.content for f in files) == SAMPLE_DIFF

    def test_empty_input_returns_empty_list(self):
        assert split_unified_diff("") == []

    def test_input_without_diff_header_returns_empty_list(self):
        # Defensive: gh always emits headers, but malformed input shouldn't crash.
        assert split_unified_diff("not a diff\nat all\n") == []

    def test_path_with_subdirectory_is_preserved(self):
        files = split_unified_diff(SAMPLE_DIFF)
        assert files[1].path == "bar/baz.py"


def _make_file(path: str, size: int) -> FileDiff:
    """Helper: produce a FileDiff with a content of exactly `size` chars."""
    header = f"diff --git a/{path} b/{path}\n"
    body = "x" * (size - len(header))
    assert len(header + body) == size
    return FileDiff(path=path, content=header + body)


class TestPackChunks:
    def test_empty_input(self):
        assert pack_chunks([], max_chars=1000) == []

    def test_single_small_file_one_chunk(self):
        files = [_make_file("a.py", 100)]
        chunks = pack_chunks(files, max_chars=1000)
        assert len(chunks) == 1
        assert chunks[0].files == ["a.py"]
        assert chunks[0].truncated_files == []
        assert chunks[0].content == files[0].content

    def test_files_under_threshold_combine_into_one_chunk(self):
        files = [_make_file(f"f{i}.py", 100) for i in range(5)]
        chunks = pack_chunks(files, max_chars=1000)
        assert len(chunks) == 1
        assert sorted(chunks[0].files) == sorted([f.path for f in files])
        assert chunks[0].truncated_files == []

    def test_ffd_ordering_packs_efficiently(self):
        # 25 KB + 8 KB + 3 KB with a 30 KB threshold should produce 2 chunks.
        # FFD: big alone first, then small fits with big (25+3=28 ≤ 30),
        # mid stays solo since 25+8 > 30.
        big = _make_file("big.py", 25000)
        mid = _make_file("mid.py", 8000)
        small = _make_file("small.py", 3000)
        chunks = pack_chunks([small, big, mid], max_chars=30000)
        assert len(chunks) == 2
        assert chunks[0].files == ["big.py", "small.py"]
        assert chunks[1].files == ["mid.py"]
        for c in chunks:
            assert len(c.content) <= 30000

    def test_oversized_file_becomes_truncated_solo_chunk(self):
        huge = _make_file("huge.py", 50000)
        chunks = pack_chunks([huge], max_chars=30000)
        assert len(chunks) == 1
        assert chunks[0].files == ["huge.py"]
        assert chunks[0].truncated_files == ["huge.py"]
        assert len(chunks[0].content) == 30000
        # First chars preserved (the diff header is what the LLM needs most).
        assert chunks[0].content.startswith("diff --git a/huge.py")

    def test_chunk_content_never_exceeds_max_chars(self):
        files = [_make_file(f"f{i}.py", size) for i, size in enumerate([5000, 7000, 8000, 12000])]
        chunks = pack_chunks(files, max_chars=15000)
        for c in chunks:
            assert len(c.content) <= 15000


def _make_hunked_file(path: str, hunk_count: int, hunk_size: int) -> FileDiff:
    """A FileDiff with a real header and `hunk_count` hunks of ~hunk_size chars."""
    header = f"diff --git a/{path} b/{path}\nindex 111..222 100644\n--- a/{path}\n+++ b/{path}\n"
    hunks = ""
    for i in range(hunk_count):
        line = f"@@ -{i*10},1 +{i*10},1 @@\n"
        body = "+" + "x" * (hunk_size - len(line) - 2) + "\n"
        hunks += line + body
    return FileDiff(path=path, content=header + hunks)


class TestOversizedFileHunkSplitting:
    def test_oversized_file_split_at_hunk_boundaries(self):
        f = _make_hunked_file("big.py", hunk_count=10, hunk_size=500)
        chunks = pack_chunks([f], max_chars=2000)
        assert len(chunks) > 1
        # Nothing truncated: every hunk fits a chunk alongside the header.
        assert all(c.truncated_files == [] for c in chunks)
        for c in chunks:
            assert len(c.content) <= 2000
            assert c.files == ["big.py"]
            # Header is repeated so each chunk keeps the file context.
            assert c.content.startswith("diff --git a/big.py")
            assert "@@ " in c.content

    def test_all_hunks_preserved_across_chunks(self):
        f = _make_hunked_file("big.py", hunk_count=10, hunk_size=500)
        chunks = pack_chunks([f], max_chars=2000)
        total_hunks = sum(c.content.count("@@ ") for c in chunks)
        assert total_hunks == 10

    def test_other_files_not_packed_into_split_chunks(self):
        big = _make_hunked_file("big.py", hunk_count=10, hunk_size=500)
        small = _make_file("small.py", 100)
        chunks = pack_chunks([big, small], max_chars=2000)
        for c in chunks:
            if "big.py" in c.files:
                assert c.files == ["big.py"]

    def test_single_giant_hunk_falls_back_to_truncation(self):
        f = _make_hunked_file("big.py", hunk_count=1, hunk_size=5000)
        chunks = pack_chunks([f], max_chars=2000)
        assert len(chunks) == 1
        assert chunks[0].truncated_files == ["big.py"]
        assert len(chunks[0].content) <= 2000


class TestFilterIgnoredFiles:
    def _files(self, *paths: str) -> list[FileDiff]:
        return [FileDiff(path=p, content=f"diff --git a/{p} b/{p}\n") for p in paths]

    def test_no_patterns_keeps_everything(self):
        files = self._files("a.py", "package-lock.json")
        kept, ignored = filter_ignored_files(files, ())
        assert [f.path for f in kept] == ["a.py", "package-lock.json"]
        assert ignored == []

    def test_default_globs_drop_lockfiles_and_artifacts(self):
        files = self._files(
            "app/main.py",
            "package-lock.json",
            "frontend/package-lock.json",
            "uv.lock",
            "dist/bundle.min.js",
            "frontend/dist/app.js",
            "assets/logo.svg",
            "src/api.generated.ts",
        )
        kept, ignored = filter_ignored_files(files, DEFAULT_IGNORE_GLOBS)
        assert [f.path for f in kept] == ["app/main.py"]
        assert len(ignored) == 7

    def test_custom_pattern_matches_basename(self):
        files = self._files("deep/nested/secret.env", "app/main.py")
        kept, ignored = filter_ignored_files(files, ("*.env",))
        assert [f.path for f in kept] == ["app/main.py"]
        assert ignored == ["deep/nested/secret.env"]
