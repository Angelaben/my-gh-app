# PR Activity Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a PR is loaded, detect new comments and replies since last visit, highlight them, surface a context-aware "Fix with comment" button, and add a Recap tab summarising all new activity.

**Architecture:** Three additive layers — (1) backend enriches comments with threading and visit-tracking metadata, (2) API propagates that metadata to the frontend, (3) frontend renders badges, toasts, and a new Recap tab using the metadata without new endpoints.

**Tech Stack:** Python 3.12, FastAPI, dataclasses, pytest · Svelte 5 (runes), TypeScript, Vite

---

## File Map

**Create:**
- `tests/unit/test_comment_threads.py`
- `frontend/src/components/RecapTab.svelte`

**Modify:**
- `app/domain/models.py` — add 5 fields to `Comment`
- `app/ports/cache_port.py` — add 4 abstract methods
- `app/ports/vcs_port.py` — add 1 abstract method
- `app/ports/ai_provider.py` — add `thread` param to `stream_fix`
- `app/adapters/cache/json_file_cache.py` — implement 4 new cache methods
- `app/adapters/vcs/github_cli_adapter.py` — map `id`+`in_reply_to_id` for inline comments; add `get_authenticated_user`
- `app/services/comment_service.py` — add module-level functions + `get_enriched_comments` method + `_collect_comments` fixes
- `app/services/fix_service.py` — thread param propagation
- `app/adapters/ai/opencode_adapter.py` — thread-aware prompt
- `app/main.py` — update `GET /api/pr/...` + `ImplementFix` schema + fix endpoint
- `frontend/src/lib/types.ts` — add fields to `Comment`, add `ThreadMessage`
- `frontend/src/stores/prs.ts` — add `newCommentIds` store, update `loadComments`, update `activeTab` type
- `frontend/src/components/CommentsTab.svelte` — toast, sort-new-first, thread map, pass `thread`/`isNew` props
- `frontend/src/components/CommentCard.svelte` — `isNew` badge, reply badge, "Fix with comment" button
- `frontend/src/components/PRDetail.svelte` — add Recap tab

---

## Task 1 — Extend Comment domain model + map IDs in GitHub adapter

**Files:**
- Modify: `app/domain/models.py`
- Modify: `app/adapters/vcs/github_cli_adapter.py`

- [ ] **Step 1: Add new fields to Comment dataclass**

In `app/domain/models.py`, replace the `Comment` dataclass with:

```python
@dataclass
class Comment:
    id: int
    author: str
    body: str
    file: str | None = None
    line: int | None = None
    created_at: str | None = None
    in_reply_to_id: int | None = None   # GitHub threading — inline review comments only
    thread_id: int | None = None         # root comment ID of this thread (computed)
    is_ours: bool = False                # author == authenticated GitHub login
    is_new_reply: bool = False           # reply to our comment, created after last visit
    has_new_replies: bool = False        # set on root comment when thread has new replies
```

- [ ] **Step 2: Map `id` and `in_reply_to_id` in the GitHub adapter**

In `app/adapters/vcs/github_cli_adapter.py`, inside `get_comments()`, replace the `review_comments` mapping block:

```python
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
```

- [ ] **Step 3: Verify the app still starts**

```bash
cd /path/to/repo && python -c "from app.domain.models import Comment; c = Comment(id=1, author='x', body='y'); print(c)"
```

Expected output: `Comment(id=1, author='x', body='y', file=None, line=None, created_at=None, in_reply_to_id=None, thread_id=None, is_ours=False, is_new_reply=False, has_new_replies=False)`

- [ ] **Step 4: Commit**

```bash
git add app/domain/models.py app/adapters/vcs/github_cli_adapter.py
git commit -m "feat: extend Comment model with threading fields; map id+in_reply_to_id in GitHub adapter"
```

---

## Task 2 — Extend CachePort + implement in JsonFileCache

**Files:**
- Modify: `app/ports/cache_port.py`
- Modify: `app/adapters/cache/json_file_cache.py`

- [ ] **Step 1: Add abstract methods to CachePort**

In `app/ports/cache_port.py`, add these 4 methods (and add `from datetime import datetime` at the top):

```python
from datetime import datetime

# ... existing class body ...

    @abstractmethod
    def get_last_visited(self, repo_full_name: str, pr_number: int) -> datetime | None:
        """Return the datetime the PR was last loaded, or None if never visited."""
        ...

    @abstractmethod
    def set_last_visited(self, repo_full_name: str, pr_number: int, dt: datetime) -> None:
        """Persist the datetime the PR was loaded."""
        ...

    @abstractmethod
    def get_github_login(self) -> str | None:
        """Return the cached authenticated GitHub login, or None."""
        ...

    @abstractmethod
    def set_github_login(self, login: str) -> None:
        """Persist the authenticated GitHub login."""
        ...
```

- [ ] **Step 2: Add a `_visits_dir` to JsonFileCache.__init__**

In `app/adapters/cache/json_file_cache.py`, update `__init__` and `_ensure_dirs`:

```python
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
```

- [ ] **Step 3: Implement the 4 new cache methods in JsonFileCache**

Add at the end of the `JsonFileCache` class (also add `from datetime import datetime, timezone` at the top of the file):

```python
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
```

- [ ] **Step 4: Verify**

```bash
python -c "
from pathlib import Path
from datetime import datetime, timezone
import tempfile, os
with tempfile.TemporaryDirectory() as tmp:
    from app.adapters.cache.json_file_cache import JsonFileCache
    c = JsonFileCache(Path(tmp))
    assert c.get_last_visited('a/b', 1) is None
    now = datetime.now(timezone.utc)
    c.set_last_visited('a/b', 1, now)
    assert c.get_last_visited('a/b', 1).isoformat() == now.isoformat()
    assert c.get_github_login() is None
    c.set_github_login('alice')
    assert c.get_github_login() == 'alice'
    print('OK')
"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add app/ports/cache_port.py app/adapters/cache/json_file_cache.py
git commit -m "feat: add last_visited and github_login to CachePort + JsonFileCache"
```

---

## Task 3 — Add `get_authenticated_user` to VCSPort + GitHubCLIAdapter

**Files:**
- Modify: `app/ports/vcs_port.py`
- Modify: `app/adapters/vcs/github_cli_adapter.py`

- [ ] **Step 1: Add abstract method to VCSPort**

In `app/ports/vcs_port.py`, add at the end of the class:

```python
    @abstractmethod
    def get_authenticated_user(self) -> str:
        """Return the GitHub login of the currently authenticated user."""
        ...
```

- [ ] **Step 2: Implement in GitHubCLIAdapter**

In `app/adapters/vcs/github_cli_adapter.py`, add at the end of the class:

```python
    def get_authenticated_user(self) -> str:
        raw = self._run(["api", "user", "--jq", ".login"])
        return raw.strip()
```

- [ ] **Step 3: Verify**

```bash
python -c "
from app.adapters.vcs.github_cli_adapter import GitHubCLIAdapter
vcs = GitHubCLIAdapter()
login = vcs.get_authenticated_user()
print('login:', login)
assert isinstance(login, str) and len(login) > 0
"
```

Expected: `login: <your-github-username>`

- [ ] **Step 4: Commit**

```bash
git add app/ports/vcs_port.py app/adapters/vcs/github_cli_adapter.py
git commit -m "feat: add get_authenticated_user to VCSPort + GitHubCLIAdapter"
```

---

## Task 4 — Add `build_threads`, `enrich_comments`, helper functions + tests

**Files:**
- Modify: `app/services/comment_service.py`
- Create: `tests/unit/test_comment_threads.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_comment_threads.py`:

```python
from datetime import datetime, timezone
from app.domain.models import Comment
from app.services.comment_service import build_threads, enrich_comments


def _c(id: int, author: str, body: str = "x",
       in_reply_to_id: int | None = None,
       created_at: str = "2026-04-20T10:00:00Z") -> Comment:
    return Comment(id=id, author=author, body=body,
                   in_reply_to_id=in_reply_to_id, created_at=created_at)


_BEFORE = datetime(2026, 4, 20, 9, 0, 0, tzinfo=timezone.utc)   # last_visited_at
_AFTER  = "2026-04-20T10:00:00Z"                                  # created_at > _BEFORE
_BEFORE_STR = "2026-04-20T08:00:00Z"                              # created_at < _BEFORE


class TestBuildThreads:
    def test_single_comment_is_its_own_thread(self):
        c = _c(1, "alice")
        threads = build_threads([c])
        assert list(threads.keys()) == [1]
        assert threads[1] == [c]

    def test_reply_grouped_under_root(self):
        root = _c(1, "alice")
        reply = _c(2, "bob", in_reply_to_id=1)
        threads = build_threads([root, reply])
        assert set(threads.keys()) == {1}
        assert set(c.id for c in threads[1]) == {1, 2}

    def test_two_independent_threads(self):
        c1 = _c(1, "alice")
        c2 = _c(2, "bob", in_reply_to_id=1)
        c3 = _c(3, "carol")
        threads = build_threads([c1, c2, c3])
        assert set(threads.keys()) == {1, 3}
        assert set(c.id for c in threads[1]) == {1, 2}
        assert set(c.id for c in threads[3]) == {3}

    def test_orphan_reply_becomes_own_thread(self):
        # in_reply_to_id points to non-existent parent
        reply = _c(2, "bob", in_reply_to_id=999)
        threads = build_threads([reply])
        assert set(threads.keys()) == {2}

    def test_empty_list(self):
        assert build_threads([]) == {}


class TestEnrichComments:
    def test_new_non_ours_comment_added_to_new_ids(self):
        c = _c(1, "alice", created_at=_AFTER)
        _, new_ids = enrich_comments([c], our_login="me", last_visited_at=_BEFORE)
        assert 1 in new_ids

    def test_old_comment_not_in_new_ids(self):
        c = _c(1, "alice", created_at=_BEFORE_STR)
        _, new_ids = enrich_comments([c], our_login="me", last_visited_at=_BEFORE)
        assert 1 not in new_ids

    def test_our_new_comment_not_in_new_ids(self):
        c = _c(1, "me", created_at=_AFTER)
        _, new_ids = enrich_comments([c], our_login="me", last_visited_at=_BEFORE)
        assert 1 not in new_ids

    def test_is_ours_flag_set(self):
        c = _c(1, "me")
        enriched, _ = enrich_comments([c], our_login="me", last_visited_at=None)
        assert enriched[0].is_ours is True

    def test_is_ours_false_for_others(self):
        c = _c(1, "alice")
        enriched, _ = enrich_comments([c], our_login="me", last_visited_at=None)
        assert enriched[0].is_ours is False

    def test_thread_id_set_to_root(self):
        root = _c(1, "me")
        reply = _c(2, "alice", in_reply_to_id=1)
        enriched, _ = enrich_comments([root, reply], our_login="me", last_visited_at=None)
        by_id = {c.id: c for c in enriched}
        assert by_id[1].thread_id == 1
        assert by_id[2].thread_id == 1

    def test_is_new_reply_when_reply_to_ours_after_visit(self):
        root  = _c(1, "me",    created_at=_BEFORE_STR)
        reply = _c(2, "alice", created_at=_AFTER, in_reply_to_id=1)
        enriched, _ = enrich_comments([root, reply], our_login="me", last_visited_at=_BEFORE)
        by_id = {c.id: c for c in enriched}
        assert by_id[2].is_new_reply is True

    def test_is_new_reply_false_when_reply_is_old(self):
        root  = _c(1, "me",    created_at=_BEFORE_STR)
        reply = _c(2, "alice", created_at=_BEFORE_STR, in_reply_to_id=1)
        enriched, _ = enrich_comments([root, reply], our_login="me", last_visited_at=_BEFORE)
        by_id = {c.id: c for c in enriched}
        assert by_id[2].is_new_reply is False

    def test_has_new_replies_set_on_root(self):
        root  = _c(1, "me",    created_at=_BEFORE_STR)
        reply = _c(2, "alice", created_at=_AFTER, in_reply_to_id=1)
        enriched, _ = enrich_comments([root, reply], our_login="me", last_visited_at=_BEFORE)
        by_id = {c.id: c for c in enriched}
        assert by_id[1].has_new_replies is True
        assert by_id[2].has_new_replies is False

    def test_no_has_new_replies_when_reply_by_us(self):
        root  = _c(1, "me",  created_at=_BEFORE_STR)
        reply = _c(2, "me",  created_at=_AFTER, in_reply_to_id=1)
        enriched, _ = enrich_comments([root, reply], our_login="me", last_visited_at=_BEFORE)
        by_id = {c.id: c for c in enriched}
        assert by_id[1].has_new_replies is False

    def test_no_last_visited_means_nothing_is_new(self):
        c = _c(1, "alice", created_at=_AFTER)
        enriched, new_ids = enrich_comments([c], our_login="me", last_visited_at=None)
        assert new_ids == []
        assert enriched[0].is_new_reply is False
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /path/to/repo && python -m pytest tests/unit/test_comment_threads.py -v 2>&1 | head -30
```

Expected: `ImportError` or `AttributeError` (functions don't exist yet)

- [ ] **Step 3: Add functions to comment_service.py**

At the top of `app/services/comment_service.py`, add imports:

```python
from dataclasses import replace
from datetime import datetime, timezone
```

Then add these module-level functions BEFORE the `CommentService` class:

```python
def _parse_dt(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))


def build_threads(comments: list[Comment]) -> dict[int, list[Comment]]:
    """Return {root_id: [root, reply1, reply2, ...]} for all comments."""
    by_id: dict[int, Comment] = {c.id: c for c in comments if c.id}

    def get_root_id(c: Comment) -> int:
        if c.in_reply_to_id is None or c.in_reply_to_id not in by_id:
            return c.id
        return get_root_id(by_id[c.in_reply_to_id])

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
    is_ours_map = {c.id: c.author == our_login for c in comments}
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
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
python -m pytest tests/unit/test_comment_threads.py -v
```

Expected: all 16 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/comment_service.py tests/unit/test_comment_threads.py
git commit -m "feat: add build_threads + enrich_comments to comment_service; add unit tests"
```

---

## Task 5 — Add `get_enriched_comments` + update `_collect_comments` + update main.py

**Files:**
- Modify: `app/services/comment_service.py`
- Modify: `app/main.py`

- [ ] **Step 1: Fix `_collect_comments` to include `id` and `in_reply_to_id`**

In `app/services/comment_service.py`, replace `_collect_comments`:

```python
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
                })

        for rc in data.get("review_comments", []):
            result.append(rc | {
                "author": _normalize_author(rc.get("author", "")),
                "in_reply_to_id": rc.get("in_reply_to_id"),
            })

        return result
```

- [ ] **Step 2: Add `_dict_to_comment` helper and `get_enriched_comments` method**

Add this module-level helper BEFORE the `CommentService` class (after the other module-level functions):

```python
def _dict_to_comment(d: dict) -> Comment:
    return Comment(
        id=d.get("id") or 0,
        author=d.get("author", ""),
        body=d.get("body", ""),
        file=d.get("path") or d.get("file"),
        line=d.get("line"),
        created_at=d.get("created_at"),
        in_reply_to_id=d.get("in_reply_to_id"),
    )
```

Add this method to the `CommentService` class after `get_comments`:

```python
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
```

- [ ] **Step 3: Update `GET /api/pr/{owner}/{repo}/{pr_number}` in main.py**

Add imports at the top of `app/main.py`:

```python
from dataclasses import asdict
from datetime import datetime, timezone
from app.services.comment_service import CommentService
```

Replace the `get_pr_detail` endpoint:

```python
@app.get("/api/pr/{owner}/{repo}/{pr_number}")
def get_pr_detail(owner: str, repo: str, pr_number: int):
    try:
        repo_full_name = f"{owner}/{repo}"

        our_login = _cache.get_github_login()
        if our_login is None:
            our_login = _vcs.get_authenticated_user()
            _cache.set_github_login(our_login)

        last_visited_at = _cache.get_last_visited(repo_full_name, pr_number)

        enriched, new_comment_ids = _comment_service.get_enriched_comments(
            repo_full_name, pr_number, our_login, last_visited_at
        )

        _cache.set_last_visited(repo_full_name, pr_number, datetime.now(timezone.utc))

        return {
            "comments": [asdict(c) for c in enriched],
            "new_comment_ids": new_comment_ids,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

- [ ] **Step 4: Update `ImplementFix` schema and the fix endpoint**

Replace `ImplementFix` in `app/main.py`:

```python
class ImplementFix(BaseModel):
    repo: str
    pr_number: int
    comment_body: str
    thread: list[dict] | None = None  # [{"author": str, "body": str}, ...]
```

Update the `implement_fix` endpoint to pass `thread`:

```python
@app.post("/api/comment/fix")
async def implement_fix(
    data: ImplementFix, svc: FixService = Depends(get_fix_service)
):
    async def event_stream():
        async for event in svc.stream_fix(
            data.repo, data.pr_number, data.comment_body, data.thread
        ):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 5: Smoke-test the endpoint starts**

```bash
python -c "from app.main import app; print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add app/services/comment_service.py app/main.py
git commit -m "feat: add get_enriched_comments; update GET /api/pr endpoint and ImplementFix schema"
```

---

## Task 6 — Thread-aware stream_fix in AIProvider, OpenCodeAdapter, FixService

**Files:**
- Modify: `app/ports/ai_provider.py`
- Modify: `app/adapters/ai/opencode_adapter.py`
- Modify: `app/services/fix_service.py`

- [ ] **Step 1: Update `stream_fix` signature in AIProvider port**

In `app/ports/ai_provider.py`, replace the `stream_fix` abstract method:

```python
    @abstractmethod
    async def stream_fix(
        self,
        repo_dir: str,
        repo_full_name: str,
        pr_number: int,
        comment_body: str,
        thread: list[dict] | None = None,
    ) -> AsyncGenerator[FixChunkEvent, None]:
        """Stream fix implementation output.

        thread: ordered list of {"author": str, "body": str} dicts for the full
        discussion thread. When provided, the AI incorporates the discussion context.
        """
        yield
```

- [ ] **Step 2: Add thread-aware prompt template to opencode_adapter.py**

In `app/adapters/ai/opencode_adapter.py`, add after `_FIX_PROMPT_TEMPLATE`:

```python
_FIX_WITH_THREAD_PROMPT_TEMPLATE = """You are fixing a code review comment on PR #{pr_number} in {repo_full_name}.
The original review comment was:

{comment_body}

A discussion followed on that comment:
{thread_text}

You are currently in the repository checkout on the PR branch.
Read the relevant files, understand the issue and the full discussion, and EDIT the files to implement the fix.
Take into account all points raised in the discussion.
Make minimal, targeted changes. Do NOT create new files unless absolutely necessary.
Do NOT run tests or build commands — just make the code changes."""
```

- [ ] **Step 3: Update `stream_fix` in OpenCodeAdapter**

In `app/adapters/ai/opencode_adapter.py`, replace `stream_fix`:

```python
    async def stream_fix(
        self,
        repo_dir: str,
        repo_full_name: str,
        pr_number: int,
        comment_body: str,
        thread: list[dict] | None = None,
    ) -> AsyncGenerator[FixChunkEvent, None]:
        self._write_opencode_config(repo_dir)

        if thread and len(thread) > 1:
            thread_text = "\n".join(
                f"- {msg['author']}: {msg['body']}"
                for msg in thread[1:]  # skip index 0 which is the original comment
            )
            prompt = _FIX_WITH_THREAD_PROMPT_TEMPLATE.format(
                pr_number=pr_number,
                repo_full_name=repo_full_name,
                comment_body=comment_body,
                thread_text=thread_text,
            )
        else:
            prompt = _FIX_PROMPT_TEMPLATE.format(
                pr_number=pr_number,
                repo_full_name=repo_full_name,
                comment_body=comment_body,
            )

        async for chunk in _stream_opencode(prompt, cwd=repo_dir, timeout=600):
            yield FixChunkEvent(text=chunk)
```

- [ ] **Step 4: Update `stream_fix` in FixService**

In `app/services/fix_service.py`, update the `stream_fix` signature and the `self._ai.stream_fix` call:

```python
    async def stream_fix(
        self,
        repo_full_name: str,
        pr_number: int,
        comment_body: str,
        thread: list[dict] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Stream fix implementation. Yields status/chunk/result/error/done dicts."""
        try:
            yield {"type": "status", "text": "Fetching PR info..."}
            pr_branch = self._vcs.get_pr_head_branch(repo_full_name, pr_number)

            yield {"type": "status", "text": f"Preparing worktree for PR #{pr_number}..."}
            wt_path = self._worktree.create(repo_full_name, pr_number, pr_branch)
            yield {"type": "status", "text": f"Worktree ready at {wt_path}"}

            yield {"type": "status", "text": "Running AI to implement fix..."}
            full_output: list[str] = []
            async for chunk in self._ai.stream_fix(
                wt_path, repo_full_name, pr_number, comment_body, thread
            ):
                full_output.append(chunk.text)
                yield {"type": "chunk", "text": chunk.text}

            if not self._worktree.has_changes(wt_path):
                yield {"type": "error", "text": "AI did not make any file changes."}
                yield {
                    "type": "result",
                    "worktree_path": wt_path,
                    "has_changes": False,
                    "diff": "",
                    "text": "".join(full_output).strip(),
                }
            else:
                self._worktree.stage_all(wt_path)
                diff_text = self._worktree.get_staged_diff(wt_path)
                yield {"type": "status", "text": f"Changes ready in {wt_path}"}
                yield {
                    "type": "result",
                    "worktree_path": wt_path,
                    "branch": pr_branch,
                    "has_changes": True,
                    "diff": diff_text[:_MAX_DIFF_RESULT_CHARS],
                    "text": "".join(full_output).strip(),
                }

            yield {"type": "done"}

        except Exception as e:
            logger.exception("Fix flow failed")
            yield {"type": "error", "text": str(e)}
            yield {"type": "done"}
```

- [ ] **Step 5: Verify imports still resolve**

```bash
python -c "from app.services.fix_service import FixService; from app.adapters.ai.opencode_adapter import OpenCodeAdapter; print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add app/ports/ai_provider.py app/adapters/ai/opencode_adapter.py app/services/fix_service.py
git commit -m "feat: thread-aware stream_fix across AIProvider, OpenCodeAdapter, FixService"
```

---

## Task 7 — Update frontend types + prs store

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/stores/prs.ts`

- [ ] **Step 1: Update `Comment` interface + add `ThreadMessage`**

In `frontend/src/lib/types.ts`, replace the `Comment` interface and add `ThreadMessage`:

```typescript
export interface Comment {
  id: number;
  author: string;
  body: string;
  file?: string;
  line?: number;
  created_at: string;
  analysis?: CommentAnalysis;
  // Threading and activity metadata
  in_reply_to_id?: number;
  thread_id?: number;
  is_ours?: boolean;
  is_new_reply?: boolean;
  has_new_replies?: boolean;
}

export interface ThreadMessage {
  author: string;
  body: string;
}
```

Also update `activeTab` type export (used in prs store):

```typescript
export type ActiveTab = 'review' | 'comments' | 'recap';
```

- [ ] **Step 2: Update prs store**

Replace the full contents of `frontend/src/stores/prs.ts`:

```typescript
import { writable } from 'svelte/store';
import type { PR, Review, Comment, CommentAnalysis, ActiveTab } from '../lib/types';
import { api } from '../lib/api';

export const prs = writable<PR[]>([]);
export const activePR = writable<PR | null>(null);
export const activeTab = writable<ActiveTab>('review');
export const cachedReview = writable<Review | null>(null);
export const comments = writable<Comment[]>([]);
export const newCommentIds = writable<number[]>([]);

export async function loadPRs(owner: string, repo: string, forceRefresh = false): Promise<void> {
  if (forceRefresh) {
    const data = await api.post<PR[]>(`/prs/${owner}/${repo}/refresh`);
    prs.set(data);
  } else {
    const data = await api.get<PR[]>(`/prs/${owner}/${repo}`);
    prs.set(data);
  }
}

export async function loadComments(owner: string, repo: string, prNumber: number): Promise<void> {
  const data = await api.get<{ comments: Comment[]; new_comment_ids: number[] }>(
    `/pr/${owner}/${repo}/${prNumber}`
  );
  comments.set(data.comments ?? []);
  newCommentIds.set(data.new_comment_ids ?? []);
}

export async function analyzeComments(owner: string, repo: string, prNumber: number): Promise<void> {
  const data = await api.post<{ comments: RawComment[]; analysis: RawAnalysis[] }>(
    `/comments/${owner}/${repo}/${prNumber}/analyze`
  );
  const authorStr = (author: string | { login: string }) =>
    typeof author === 'string' ? author : author.login;
  const analysisMap = new Map(data.analysis.map((a) => [a.author, a]));

  comments.update((current) =>
    current.map((c) => {
      const analysis = analysisMap.get(c.author);
      if (!analysis) return c;
      return {
        ...c,
        analysis: {
          valid: analysis.valid,
          interest: analysis.interest as CommentAnalysis['interest'],
          critical: analysis.criticality === 'P0',
          priority: analysis.criticality as CommentAnalysis['priority'],
        },
      };
    })
  );
}

export async function publishFinding(
  owner: string,
  repo: string,
  prNumber: number,
  finding: import('../lib/types').Finding
): Promise<void> {
  const fullRepo = `${owner}/${repo}`;
  const body = finding.suggestion
    ? `**${finding.priority}: ${finding.title}**\n\n${finding.description}\n\n**Suggestion:**\n${finding.suggestion}`
    : `**${finding.priority}: ${finding.title}**\n\n${finding.description}`;

  if (finding.file && finding.line != null) {
    await api.post('/comment/inline', {
      repo: fullRepo,
      pr_number: prNumber,
      body,
      path: finding.file,
      line: finding.line,
    });
  } else {
    await api.post('/comment/publish', {
      repo: fullRepo,
      pr_number: prNumber,
      body,
    });
  }
}

// Internal types for raw API shapes
interface RawComment {
  author: string | { login: string };
  body: string;
  path?: string;
  line?: number;
  created_at: string;
}

interface RawAnalysis {
  author: string;
  criticality: string;
  valid: boolean;
  interest: string;
  summary: string;
  original_body: string;
}
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd /path/to/repo/frontend && npx tsc --noEmit 2>&1 | head -30
```

Expected: no errors (or only pre-existing errors unrelated to this change)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/stores/prs.ts
git commit -m "feat: update Comment type with threading fields; add newCommentIds store; update activeTab type"
```

---

## Task 8 — Update CommentsTab (toast, sort, thread map, props)

**Files:**
- Modify: `frontend/src/components/CommentsTab.svelte`

- [ ] **Step 1: Replace CommentsTab.svelte**

```svelte
<script lang="ts">
  import { onMount } from 'svelte';
  import { get } from 'svelte/store';
  import { comments, newCommentIds, loadComments, analyzeComments } from '../stores/prs';
  import { showToast } from '../stores/ui';
  import type { Repo, PR, ThreadMessage } from '../lib/types';
  import CommentCard from './CommentCard.svelte';

  let { repo, pr }: { repo: Repo; pr: PR } = $props();

  let loading = $state(true);
  let analyzing = $state(false);
  let filterAuthor = $state('');
  let filterPriority = $state('');

  onMount(async () => {
    try {
      await loadComments(repo.owner, repo.name, pr.number);
      const ids = get(newCommentIds);
      if (ids.length > 0) {
        const s = ids.length > 1 ? 's' : '';
        showToast(`${ids.length} nouveau${s} commentaire${s} depuis ta dernière visite`, 'info');
      }
      await analyzeComments(repo.owner, repo.name, pr.number);
    } catch {
      showToast('Failed to load comments', 'error');
    } finally {
      loading = false;
    }
  });

  async function handleAnalyze() {
    analyzing = true;
    try {
      await analyzeComments(repo.owner, repo.name, pr.number);
      showToast('Analysis complete', 'success');
    } catch {
      showToast('Analysis failed', 'error');
    } finally {
      analyzing = false;
    }
  }

  // Build thread map: thread_id → array of {author, body}
  const threadMap = $derived(
    new Map<number, ThreadMessage[]>(
      [...new Set($comments.map((c) => c.thread_id ?? c.id))].map((rootId) => [
        rootId,
        $comments
          .filter((c) => (c.thread_id ?? c.id) === rootId)
          .map((c) => ({ author: c.author, body: c.body })),
      ])
    )
  );

  const authors = $derived([...new Set($comments.map((c) => c.author))]);
  const hasAnalysis = $derived($comments.some((c) => c.analysis));

  const filtered = $derived(
    $comments
      .filter((c) => {
        if (filterAuthor && c.author !== filterAuthor) return false;
        if (filterPriority && c.analysis?.priority !== filterPriority) return false;
        return true;
      })
      .sort((a, b) => {
        // New comments (unread) bubble to top
        const aNew = $newCommentIds.includes(a.id) || !!a.is_new_reply;
        const bNew = $newCommentIds.includes(b.id) || !!b.is_new_reply;
        if (aNew && !bNew) return -1;
        if (!aNew && bNew) return 1;
        return 0;
      })
  );
</script>

<div class="comments-tab">
  <div class="toolbar">
    <button class="btn btn-accent" onclick={handleAnalyze} disabled={analyzing || loading}>
      {analyzing ? '…' : '⚙ Analyze Comments'}
    </button>
    <select class="filter-select" bind:value={filterAuthor}>
      <option value="">All authors</option>
      {#each authors as author}
        <option value={author}>{author}</option>
      {/each}
    </select>
    <select class="filter-select" bind:value={filterPriority} disabled={!hasAnalysis}>
      <option value="">All severities</option>
      <option value="P0">P0</option>
      <option value="P1">P1</option>
      <option value="P2">P2</option>
      <option value="P3">P3</option>
    </select>
    <span class="count">{filtered.length} / {$comments.length}</span>
  </div>

  {#if loading}
    <div class="loading-state">
      <div class="spinner"></div>
      <span style="color:var(--text-muted)">Loading comments…</span>
    </div>
  {:else if filtered.length === 0}
    <div class="empty-state-inline">No comments match filters.</div>
  {:else}
    {#each filtered as comment (comment.id)}
      <CommentCard
        {comment}
        {repo}
        {pr}
        isNew={$newCommentIds.includes(comment.id)}
        thread={threadMap.get(comment.thread_id ?? comment.id)}
      />
    {/each}
  {/if}
</div>

<style>
  .comments-tab { display: flex; flex-direction: column; gap: 10px; }

  .toolbar {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }

  .filter-select {
    background: var(--glass-bg);
    border: 1px solid var(--glass-border);
    border-radius: var(--radius-sm);
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 11px;
    padding: 5px 8px;
    cursor: pointer;
    transition: border-color var(--transition-fast);
  }
  .filter-select:focus { outline: none; border-color: var(--border-active); }
  .filter-select:disabled { opacity: 0.4; cursor: default; }

  .count { font-size: 10px; color: var(--text-muted); margin-left: auto; }

  .loading-state {
    display: flex; align-items: center; gap: 10px; padding: 40px 0; justify-content: center;
  }
  .empty-state-inline { color: var(--text-muted); padding: 40px 0; text-align: center; font-size: 12px; }
</style>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/CommentsTab.svelte
git commit -m "feat: CommentsTab — toast on new comments, sort new-first, pass thread/isNew to cards"
```

---

## Task 9 — Update CommentCard (isNew badge, reply badge, Fix with comment)

**Files:**
- Modify: `frontend/src/components/CommentCard.svelte`

- [ ] **Step 1: Replace CommentCard.svelte**

```svelte
<script lang="ts">
  import type { Comment, Repo, PR, ThreadMessage } from '../lib/types';
  import { api } from '../lib/api';
  import { showToast } from '../stores/ui';

  let {
    comment,
    repo,
    pr,
    isNew = false,
    thread = undefined,
  }: {
    comment: Comment;
    repo: Repo;
    pr: PR;
    isNew?: boolean;
    thread?: ThreadMessage[];
  } = $props();

  type FixStatus = 'idle' | 'running' | 'done' | 'error';

  let fixStatus = $state<FixStatus>('idle');
  let fixLog = $state('');
  let fixResult: { worktree_path: string; branch: string; diff: string; has_changes: boolean } | null =
    $state(null);
  let fixError = $state('');
  let pushing = $state(false);
  let pushDone = $state(false);
  let newPrUrl = $state('');

  const replyCount = $derived((thread?.length ?? 1) - 1);
  const showFixWithComment = $derived(!!comment.is_ours && !!comment.has_new_replies);
  const showRegularFix = $derived(!showFixWithComment && !!comment.analysis);

  async function runFix(useThread = false) {
    fixStatus = 'running';
    fixLog = '';
    fixResult = null;
    fixError = '';

    const body: Record<string, unknown> = {
      repo: repo.full_name,
      pr_number: pr.number,
      comment_body: comment.body,
    };
    if (useThread && thread && thread.length > 1) {
      body.thread = thread;
    }

    const res = await fetch('/api/comment/fix', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });

    if (!res.ok || !res.body) {
      fixStatus = 'error';
      fixError = 'Request failed';
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const blocks = buffer.split('\n\n');
      buffer = blocks.pop() ?? '';
      for (const block of blocks) {
        const line = block.trim();
        if (!line.startsWith('data:')) continue;
        try {
          const ev = JSON.parse(line.slice(5).trim());
          if (ev.type === 'status' || ev.type === 'chunk') {
            fixLog += ev.text;
          } else if (ev.type === 'result') {
            fixResult = ev;
            fixStatus = 'done';
          } else if (ev.type === 'error') {
            fixError = ev.text ?? ev.message ?? 'Unknown error';
            fixStatus = 'error';
          }
        } catch {
          // malformed SSE chunk — skip
        }
      }
    }

    if (fixStatus === 'running') fixStatus = 'done';
  }

  async function pushFix() {
    if (!fixResult) return;
    pushing = true;
    try {
      await api.post('/comment/fix/push', {
        repo: repo.full_name,
        pr_number: pr.number,
        branch: fixResult.branch,
        diff: fixResult.diff,
        comment_body: comment.body,
      });
      pushDone = true;
      showToast('Fix pushed to PR branch', 'success');
    } catch {
      showToast('Push failed', 'error');
    } finally {
      pushing = false;
    }
  }

  async function newPr() {
    if (!fixResult) return;
    pushing = true;
    try {
      const res = await api.post<{ pr_url: string; pr_title: string; branch: string }>(
        '/comment/fix/new-pr',
        {
          repo: repo.full_name,
          pr_number: pr.number,
          branch: fixResult.branch,
          diff: fixResult.diff,
          comment_body: comment.body,
        }
      );
      newPrUrl = res.pr_url;
      showToast(`PR created: ${res.pr_title}`, 'success');
    } catch {
      showToast('Failed to create PR', 'error');
    } finally {
      pushing = false;
    }
  }

  const priorityClass = $derived(
    comment.analysis?.priority ? `badge-${comment.analysis.priority.toLowerCase()}` : ''
  );
</script>

<div class="comment-card" class:is-new={isNew}>
  <div class="comment-header">
    <span class="author">{comment.author}</span>

    {#if comment.file}
      <span class="file-ref">{comment.file}{comment.line ? `:${comment.line}` : ''}</span>
    {/if}

    <div class="header-badges">
      {#if isNew && !comment.is_ours}
        <span class="badge badge-new">New</span>
      {/if}

      {#if comment.is_ours && replyCount > 0}
        <span class="badge" class:badge-new-reply={comment.has_new_replies} class:badge-p3={!comment.has_new_replies}>
          💬 {replyCount} {replyCount === 1 ? 'reply' : 'replies'}
        </span>
      {/if}

      {#if comment.analysis}
        <span class="badge {priorityClass}">{comment.analysis.priority}</span>
        <span class="badge badge-p3">Interest: {comment.analysis.interest}</span>
        <span class="badge" class:badge-p0={!comment.analysis.valid} class:badge-p3={comment.analysis.valid}>
          {comment.analysis.valid ? 'Valid' : 'Invalid'}
        </span>
      {/if}
    </div>
  </div>

  <p class="comment-body">{comment.body}</p>

  {#if showFixWithComment || showRegularFix}
    <div class="comment-actions">
      {#if fixStatus === 'idle'}
        {#if showFixWithComment}
          <button class="btn btn-accent btn-sm" onclick={() => runFix(true)}>💬 Fix with comment</button>
        {:else}
          <button class="btn btn-accent btn-sm" onclick={() => runFix(false)}>⚡ Fix & Submit PR</button>
        {/if}
      {:else if fixStatus === 'running'}
        <button class="btn btn-sm" disabled>Implementing fix…</button>
      {:else if fixStatus === 'done' && fixResult?.has_changes}
        {#if pushDone}
          <span class="fix-ok">✓ Pushed to PR branch</span>
        {:else if newPrUrl}
          <span class="fix-ok">✓ PR created</span>
          <a class="pr-link" href={newPrUrl} target="_blank" rel="noreferrer">{newPrUrl}</a>
        {:else}
          <button class="btn btn-success btn-sm" onclick={pushFix} disabled={pushing}>
            {pushing ? '…' : '↑ Push to PR branch'}
          </button>
          <button class="btn btn-sm" onclick={newPr} disabled={pushing}>
            {pushing ? '…' : '+ New PR'}
          </button>
        {/if}
      {:else if fixStatus === 'done'}
        <span style="color:var(--text-muted);font-size:11px;">No changes made</span>
      {:else if fixStatus === 'error'}
        <span class="fix-err">✕ {fixError}</span>
        <button class="btn btn-sm" onclick={() => runFix(showFixWithComment)}>Retry</button>
      {/if}
    </div>
  {/if}

  {#if fixLog}
    <details class="fix-log" open={fixStatus === 'running'}>
      <summary>Fix output</summary>
      <pre class="log-body">{fixLog}</pre>
    </details>
  {/if}

  {#if fixResult?.has_changes && fixResult.diff}
    <details class="fix-log">
      <summary>Diff</summary>
      <pre class="log-body diff">{fixResult.diff}</pre>
    </details>
  {/if}
</div>

<style>
  .comment-card {
    background: var(--glass-bg);
    border: 1px solid var(--glass-border);
    border-radius: var(--radius-md);
    padding: 12px 14px;
    margin-bottom: 6px;
    animation: fadeSlide 0.25s ease both;
    transition: border-color var(--transition-base);
  }
  .comment-card:hover { border-color: var(--glass-border-hover); }
  .comment-card.is-new { border-color: var(--accent); }

  .comment-header {
    display: flex; align-items: center; gap: 8px;
    margin-bottom: 8px; flex-wrap: wrap;
  }
  .author { font-size: 11px; font-weight: 700; color: var(--accent); }
  .file-ref { font-size: 10px; color: var(--text-muted); font-style: italic; }
  .header-badges { display: flex; gap: 5px; margin-left: auto; flex-wrap: wrap; align-items: center; }
  .comment-body { font-size: 12px; color: var(--text-secondary); line-height: 1.6; white-space: pre-wrap; }

  .comment-actions {
    display: flex; align-items: center; gap: 8px; margin-top: 10px; flex-wrap: wrap;
  }

  .badge-new { background: color-mix(in srgb, var(--accent) 20%, transparent); color: var(--accent); border-color: var(--accent); }
  .badge-new-reply { background: color-mix(in srgb, orange 20%, transparent); color: orange; border-color: orange; }

  .fix-ok { font-size: 11px; color: var(--success); }
  .fix-err { font-size: 11px; color: var(--p0); }
  .pr-link { font-size: 10px; color: var(--accent); text-decoration: underline; word-break: break-all; }

  .fix-log { margin-top: 8px; }
  .fix-log summary {
    font-size: 10px; color: var(--accent); text-transform: uppercase;
    letter-spacing: 0.06em; cursor: pointer; font-weight: 600;
  }
  .fix-log summary:hover { color: var(--accent-hover); }
  .log-body {
    margin-top: 6px; font-size: 11px; color: var(--code-text);
    background: var(--code-bg); border: 1px solid var(--border-active);
    border-radius: var(--radius-sm); padding: 10px 12px;
    white-space: pre-wrap; overflow-x: auto; max-height: 300px;
    overflow-y: auto; line-height: 1.5; font-weight: 400;
  }
  .diff { color: var(--text-primary); }
</style>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/CommentCard.svelte
git commit -m "feat: CommentCard — isNew badge, reply count badge, Fix with comment button"
```

---

## Task 10 — Create RecapTab + add to PRDetail

**Files:**
- Create: `frontend/src/components/RecapTab.svelte`
- Modify: `frontend/src/components/PRDetail.svelte`

- [ ] **Step 1: Create RecapTab.svelte**

Create `frontend/src/components/RecapTab.svelte`:

```svelte
<script lang="ts">
  import { activeTab, comments, newCommentIds } from '../stores/prs';
  import type { Repo, PR } from '../lib/types';
  import { api } from '../lib/api';
  import { showToast } from '../stores/ui';

  let { repo, pr }: { repo: Repo; pr: PR } = $props();

  // New comments from others (not replies to our threads, not authored by us)
  const newIncoming = $derived(
    $comments.filter(
      (c) => $newCommentIds.includes(c.id) && !c.is_new_reply && !c.is_ours
    )
  );

  // Replies to our comments that arrived since last visit
  const newReplies = $derived($comments.filter((c) => c.is_new_reply));

  function relativeTime(isoStr: string | undefined): string {
    if (!isoStr) return '';
    const diff = Date.now() - new Date(isoStr).getTime();
    const m = Math.floor(diff / 60000);
    if (m < 1) return 'à l'instant';
    if (m < 60) return `il y a ${m} min`;
    const h = Math.floor(m / 60);
    if (h < 24) return `il y a ${h}h`;
    return `il y a ${Math.floor(h / 24)}j`;
  }

  function goToComments() {
    activeTab.set('comments');
  }

  let pushing: Record<number, boolean> = $state({});
  let pushDone: Record<number, boolean> = $state({});

  async function runFixWithThread(commentId: number, commentBody: string) {
    const thread = $comments
      .filter((c) => {
        const root = $comments.find((r) => r.id === c.thread_id);
        return root?.id === c.thread_id;
      })
      .filter((c) => (c.thread_id ?? c.id) === $comments.find((r) => r.id === commentId)?.thread_id)
      .map((c) => ({ author: c.author, body: c.body }));

    pushing = { ...pushing, [commentId]: true };
    try {
      const res = await fetch('/api/comment/fix', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          repo: repo.full_name,
          pr_number: pr.number,
          comment_body: commentBody,
          thread,
        }),
      });
      if (!res.ok) throw new Error('Failed');
      showToast('Fix started — check the Comments tab for progress', 'info');
      activeTab.set('comments');
    } catch {
      showToast('Failed to start fix', 'error');
    } finally {
      pushing = { ...pushing, [commentId]: false };
    }
  }
</script>

<div class="recap-tab">
  {#if newIncoming.length === 0 && newReplies.length === 0}
    <div class="empty-state">
      Tout est à jour — aucune activité depuis ta dernière visite.
    </div>
  {/if}

  {#if newIncoming.length > 0}
    <section class="recap-section">
      <h3 class="section-title">Nouveaux commentaires reçus</h3>
      {#each newIncoming as c (c.id)}
        <button class="recap-row" onclick={goToComments} type="button">
          <div class="row-meta">
            <span class="row-author">{c.author}</span>
            {#if c.file}
              <span class="row-file">{c.file}{c.line ? `:${c.line}` : ''}</span>
            {/if}
            <span class="row-time">{relativeTime(c.created_at)}</span>
          </div>
          <p class="row-body">{c.body}</p>
        </button>
      {/each}
    </section>
  {/if}

  {#if newReplies.length > 0}
    <section class="recap-section">
      <h3 class="section-title">Réponses à vos commentaires</h3>
      {#each newReplies as c (c.id)}
        <div class="recap-row reply-row">
          <div class="row-meta">
            <span class="row-author">{c.author}</span>
            {#if c.file}
              <span class="row-file">{c.file}{c.line ? `:${c.line}` : ''}</span>
            {/if}
            <span class="row-time">{relativeTime(c.created_at)}</span>
          </div>
          <p class="row-body">{c.body}</p>
          {#if c.thread_id != null}
            {@const root = $comments.find((r) => r.id === c.thread_id)}
            {#if root}
              <div class="reply-actions">
                <button
                  class="btn btn-accent btn-sm"
                  onclick={() => runFixWithThread(root.id, root.body)}
                  disabled={pushing[root.id]}
                >
                  {pushing[root.id] ? '…' : '💬 Fix with comment'}
                </button>
              </div>
            {/if}
          {/if}
        </div>
      {/each}
    </section>
  {/if}
</div>

<style>
  .recap-tab { display: flex; flex-direction: column; gap: 16px; }

  .empty-state {
    color: var(--text-muted); font-size: 12px;
    text-align: center; padding: 48px 0;
  }

  .recap-section { display: flex; flex-direction: column; gap: 8px; }

  .section-title {
    font-size: 10px; font-weight: 700; color: var(--text-muted);
    text-transform: uppercase; letter-spacing: 0.08em; margin: 0 0 4px;
  }

  .recap-row {
    display: block; width: 100%; text-align: left;
    background: var(--glass-bg); border: 1px solid var(--glass-border);
    border-radius: var(--radius-md); padding: 10px 12px;
    cursor: pointer; transition: border-color var(--transition-fast);
  }
  .recap-row:hover { border-color: var(--glass-border-hover); }
  .reply-row { cursor: default; }
  .reply-row:hover { border-color: var(--glass-border); }

  .row-meta {
    display: flex; align-items: center; gap: 8px;
    margin-bottom: 6px; flex-wrap: wrap;
  }
  .row-author { font-size: 11px; font-weight: 700; color: var(--accent); }
  .row-file { font-size: 10px; color: var(--text-muted); font-style: italic; }
  .row-time { font-size: 10px; color: var(--text-muted); margin-left: auto; }

  .row-body {
    font-size: 11px; color: var(--text-secondary); line-height: 1.5;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    overflow: hidden; margin: 0;
  }

  .reply-actions { margin-top: 8px; }
</style>
```

- [ ] **Step 2: Update PRDetail.svelte to add the Recap tab**

Replace the full content of `frontend/src/components/PRDetail.svelte`:

```svelte
<script lang="ts">
  import { activeTab, newCommentIds, comments } from '../stores/prs';
  import type { Repo, PR } from '../lib/types';
  import ReviewTab from './ReviewTab.svelte';
  import CommentsTab from './CommentsTab.svelte';
  import RecapTab from './RecapTab.svelte';

  let { repo, pr }: { repo: Repo; pr: PR } = $props();

  const recapCount = $derived(
    $newCommentIds.length
  );
</script>

<div class="pr-detail">
  <div class="pr-detail-header">
    <h2 class="pr-title">
      <span class="pr-number">#{pr.number}</span>
      {pr.title}
    </h2>
    <div class="pr-meta">
      <span>{pr.author}</span>
      <span class="sep">·</span>
      <span class="branch">{pr.branch} → {pr.base_branch}</span>
      <span class="sep">·</span>
      <span class="additions">+{pr.additions}</span>
      <span class="deletions">-{pr.deletions}</span>
    </div>
  </div>

  <div class="tabs">
    <button class="tab" class:active={$activeTab === 'review'} onclick={() => activeTab.set('review')}>
      Review
    </button>
    <button class="tab" class:active={$activeTab === 'comments'} onclick={() => activeTab.set('comments')}>
      Comments
    </button>
    <button class="tab" class:active={$activeTab === 'recap'} onclick={() => activeTab.set('recap')}>
      Recap{recapCount > 0 ? ` (${recapCount})` : ''}
    </button>
  </div>

  <div class="tab-content">
    {#if $activeTab === 'review'}
      <ReviewTab {repo} {pr} />
    {:else if $activeTab === 'comments'}
      <CommentsTab {repo} {pr} />
    {:else}
      <RecapTab {repo} {pr} />
    {/if}
  </div>
</div>

<style>
  .pr-detail { padding: 20px; display: flex; flex-direction: column; gap: 0; }
  .pr-detail-header {
    background: var(--glass-bg); border: 1px solid var(--glass-border);
    border-radius: var(--radius-lg); padding: 14px 18px; margin-bottom: 14px;
  }
  .pr-title {
    font-size: 14px; font-weight: 600; color: var(--text-primary);
    margin-bottom: 6px; display: flex; align-items: baseline; gap: 8px;
  }
  .pr-number { color: var(--accent); font-size: 12px; flex-shrink: 0; }
  .pr-meta { display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--text-muted); }
  .sep { color: var(--border-active); }
  .branch { color: var(--text-secondary); font-style: italic; }
  .additions { color: var(--success); }
  .deletions { color: var(--p0); }
  .tabs {
    display: flex; gap: 0;
    border-bottom: 1px solid var(--border); margin-bottom: 14px;
  }
  .tab {
    background: none; border: none; border-bottom: 2px solid transparent;
    margin-bottom: -1px; color: var(--text-muted);
    font-family: var(--font-mono); font-size: 12px; font-weight: 600;
    padding: 8px 16px; cursor: pointer;
    transition: color var(--transition-fast), border-color var(--transition-fast);
  }
  .tab:hover { color: var(--text-secondary); }
  .tab.active { color: var(--accent); border-color: var(--accent); }
</style>
```

- [ ] **Step 3: Build the frontend to verify no TypeScript/Svelte errors**

```bash
cd /path/to/repo/frontend && npm run build 2>&1 | tail -20
```

Expected: build succeeds with no errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/RecapTab.svelte frontend/src/components/PRDetail.svelte
git commit -m "feat: add RecapTab component; add Recap tab to PRDetail with unread badge count"
```

---

## Self-Review Checklist (run before handing off)

- [ ] Spec §1 (new comment detection + toast): Task 2 cache, Task 5 endpoint, Task 8 toast ✓
- [ ] Spec §2 (reply detection + Fix with comment): Task 1 model, Task 4 enrich_comments, Task 6 thread prompt, Task 9 button ✓
- [ ] Spec §3 (Recap tab): Task 10 RecapTab + PRDetail ✓
- [ ] `build_threads` and `enrich_comments` tested with 16 unit tests: Task 4 ✓
- [ ] `thread` field in `ImplementFix` is `list[dict] | None` (not `list[str]`): Task 5 ✓
- [ ] `has_new_replies` set only on root comment: Task 4 `enrich_comments` ✓
- [ ] `activeTab` type includes `'recap'`: Task 7 ✓
- [ ] `newCommentIds` store exported and used in CommentsTab and PRDetail: Tasks 7–10 ✓
- [ ] PR-level comments get unique negative synthetic IDs: Task 5 `_collect_comments` ✓
- [ ] Fix SSE parse loop missing `catch` block (pre-existing bug): fixed in Task 9 ✓
