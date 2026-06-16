"""Pure helpers for splitting a unified diff by file and bin-packing chunks.

Used by ReviewService when the PR diff exceeds the LLM context budget.
No I/O, no asyncio — keep it trivially testable.
"""
import re
from dataclasses import dataclass, field
from fnmatch import fnmatch

# Splits on every line that starts with "diff --git ". Keeps that line in the
# *next* element via the lookahead (?=...).
_DIFF_HEADER_RE = re.compile(r"(?m)^(?=diff --git )")
# Parses "diff --git a/<path> b/<path>" — captures the a/ side.
_DIFF_PATH_RE = re.compile(r"^diff --git a/(\S+) b/")
# Splits a file section on hunk boundaries, keeping "@@ " in the next element.
_HUNK_RE = re.compile(r"(?m)^(?=@@ )")

# Files that waste LLM budget without producing actionable findings:
# lockfiles, build artifacts, minified/generated code, vendored deps.
# fnmatch's `*` spans `/`, so "dist/*" also matches nested paths; patterns
# are additionally matched against the basename so "package-lock.json"
# catches "frontend/package-lock.json".
DEFAULT_IGNORE_GLOBS: tuple[str, ...] = (
    "*.lock",
    "package-lock.json",
    "go.sum",
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.svg",
    "*.snap",
    "dist/*",
    "*/dist/*",
    "build/*",
    "*/build/*",
    "node_modules/*",
    "*/node_modules/*",
    "vendor/*",
    "*/vendor/*",
    "*.generated.*",
    "*/__generated__/*",
)


@dataclass
class FileDiff:
    """One file's section inside a unified diff."""

    path: str
    content: str  # full section, starting with "diff --git "


def split_unified_diff(diff: str) -> list[FileDiff]:
    """Split a unified diff on `diff --git ` boundaries.

    Returns an empty list if the input contains no `diff --git ` header.
    Sections without a parseable path are skipped (defensive).
    """
    if not diff:
        return []
    parts = _DIFF_HEADER_RE.split(diff)
    # The first element is anything before the first header — drop it
    # (re.split with a lookahead emits an empty string here when the diff
    # starts with the header, or pre-header garbage otherwise).
    files: list[FileDiff] = []
    for part in parts[1:]:
        m = _DIFF_PATH_RE.match(part)
        if not m:
            continue
        files.append(FileDiff(path=m.group(1), content=part))
    return files


def filter_ignored_files(
    files: list[FileDiff], patterns: tuple[str, ...] | list[str]
) -> tuple[list[FileDiff], list[str]]:
    """Partition file diffs into (kept, ignored_paths) using glob patterns.

    A pattern matches when it fnmatch-es either the full path or the basename.
    """
    if not patterns:
        return files, []
    kept: list[FileDiff] = []
    ignored: list[str] = []
    for f in files:
        basename = f.path.rsplit("/", 1)[-1]
        if any(fnmatch(f.path, p) or fnmatch(basename, p) for p in patterns):
            ignored.append(f.path)
        else:
            kept.append(f)
    return kept, ignored


@dataclass
class DiffRow:
    """One displayable row of a unified diff hunk.

    ``sign`` is ``" "`` (context), ``"+"`` (added) or ``"-"`` (removed).
    ``old_line`` / ``new_line`` are 1-based line numbers on each side (the
    irrelevant side is ``None`` for add/remove rows).
    """

    sign: str
    old_line: int | None
    new_line: int | None
    text: str


_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def extract_hunk_rows(
    file_content: str, target_line: int, context: int = 4
) -> tuple[list[DiffRow], bool]:
    """Return the diff rows around ``target_line`` (new-side) for one file's diff.

    ``file_content`` is a single file section of a unified diff (as produced by
    :func:`split_unified_diff`). Returns ``(rows, found)``; ``found`` is False
    when ``target_line`` is not present on the new side of any hunk (i.e. the
    finding points at a line outside the PR diff).
    """
    rows: list[DiffRow] = []
    old_no = new_no = 0
    seen_hunk = False
    for line in file_content.splitlines():
        m = _HUNK_HEADER_RE.match(line)
        if m:
            old_no, new_no = int(m.group(1)), int(m.group(2))
            seen_hunk = True
            continue
        if not seen_hunk:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            rows.append(DiffRow("+", None, new_no, line[1:]))
            new_no += 1
        elif line.startswith("-") and not line.startswith("---"):
            rows.append(DiffRow("-", old_no, None, line[1:]))
            old_no += 1
        elif line.startswith(" "):
            rows.append(DiffRow(" ", old_no, new_no, line[1:]))
            old_no += 1
            new_no += 1
        # "\ No newline at end of file" and stray lines are ignored.

    target_idx = next(
        (
            i for i, r in enumerate(rows)
            if r.new_line == target_line and r.sign in (" ", "+")
        ),
        None,
    )
    if target_idx is None:
        return [], False
    lo = max(0, target_idx - context)
    hi = min(len(rows), target_idx + context + 1)
    return rows[lo:hi], True


@dataclass
class Chunk:
    """A bundle of file diffs sized to fit one LLM call.

    `truncated_files` lists paths whose content was cut to fit `max_chars`.
    `solo` marks chunks produced from a single oversized file (split by hunk
    or truncated) — pack_chunks never adds more files to them.
    """

    content: str
    files: list[str] = field(default_factory=list)
    truncated_files: list[str] = field(default_factory=list)
    solo: bool = False


def _split_oversized_file(f: FileDiff, max_chars: int) -> list[Chunk]:
    """Split one over-budget file at hunk boundaries into solo chunks.

    Each chunk repeats the file header (everything before the first ``@@``)
    so the LLM keeps the path/index context. Falls back to truncation only
    when there are no hunk boundaries (binary files, pure renames) or a
    single hunk alone exceeds the budget.
    """
    parts = _HUNK_RE.split(f.content)
    header, hunks = parts[0], parts[1:]
    if not hunks or len(header) >= max_chars:
        return [
            Chunk(
                content=f.content[:max_chars],
                files=[f.path],
                truncated_files=[f.path],
                solo=True,
            )
        ]

    pieces: list[Chunk] = []
    current: str | None = None
    for hunk in hunks:
        candidate = (current or header) + hunk
        if current is not None and len(candidate) > max_chars:
            pieces.append(Chunk(content=current, files=[f.path], solo=True))
            current = None
            candidate = header + hunk
        if len(candidate) > max_chars:
            pieces.append(
                Chunk(
                    content=candidate[:max_chars],
                    files=[f.path],
                    truncated_files=[f.path],
                    solo=True,
                )
            )
        else:
            current = candidate
    if current is not None:
        pieces.append(Chunk(content=current, files=[f.path], solo=True))
    return pieces


def pack_chunks(files: list[FileDiff], max_chars: int) -> list[Chunk]:
    """First-Fit Decreasing bin-packing of file diffs into chunks ≤ max_chars.

    - Files are sorted by content length descending.
    - Each file is placed in the first existing chunk where it fits, else a
      new chunk is opened.
    - A single file longer than max_chars is split at hunk boundaries into
      solo chunks (truncated only when no hunk boundary exists).

    Order of files inside a chunk is the order they were inserted (which,
    given the descending sort, means largest first).
    """
    if not files:
        return []

    sorted_files = sorted(files, key=lambda f: len(f.content), reverse=True)
    chunks: list[Chunk] = []

    for f in sorted_files:
        size = len(f.content)
        if size > max_chars:
            chunks.extend(_split_oversized_file(f, max_chars))
            continue
        placed = False
        for chunk in chunks:
            if chunk.solo or chunk.truncated_files:
                # Don't pile more files into a chunk that's already at max.
                continue
            if len(chunk.content) + size <= max_chars:
                chunk.content += f.content
                chunk.files.append(f.path)
                placed = True
                break
        if not placed:
            chunks.append(Chunk(content=f.content, files=[f.path]))

    return chunks
