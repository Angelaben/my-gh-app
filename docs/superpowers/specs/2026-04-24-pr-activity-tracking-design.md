# Design — PR Activity Tracking & Recap

**Date:** 2026-04-24
**Status:** Approved

## Context

gh-review-tool is a local PR review dashboard for solo developers. The tool currently has no notion of "what changed since I last looked at this PR." This design adds three interconnected features to close that gap:

1. Detection and highlighting of new comments since last visit
2. Detection of replies to our own comments, with a context-aware "Fix with comment" button
3. A "Recap" tab surfacing all new activity at a glance

---

## Feature 1 — New Comment Detection & Notification

### Goal

When a PR is loaded, the user immediately knows if new comments have appeared since their last visit.

### Data

Two new fields added to the per-PR cache entry (`JsonFileCache`):

```json
{
  "last_visited_at": "2026-04-23T10:00:00Z",
  "our_github_login": "angelardbenjamin"
}
```

`our_github_login` is fetched once via `gh api user` and cached globally (not per-PR). It is used across all features to identify comments authored by the current user.

### Backend changes

**`GET /api/pr/{owner}/{repo}/{pr_number}`** (existing endpoint):
- Before returning, reads `last_visited_at` from cache
- Computes `new_comment_ids: list[int]` — IDs of comments with `created_at > last_visited_at`
- Updates `last_visited_at` to `now` in cache after computing the diff
- Adds `new_comment_ids` and `our_github_login` to the response payload

**`CachePort`** gains two new methods:
- `get_last_visited(repo, pr_number) -> datetime | None`
- `set_last_visited(repo, pr_number, dt: datetime) -> None`

**`GitHubCLIAdapter`** gains one new method:
- `get_authenticated_user() -> str` — calls `gh api user --jq .login`

**`JsonFileCache`** gains one new global entry:
- `github_login.json` — stores `{ "login": "..." }`, fetched once and cached indefinitely

### Frontend changes

- **Toast notification** on PR load if `new_comment_ids.length > 0`: `"N nouveaux commentaires depuis ta dernière visite"`
- **`CommentCard`** receives an `isNew: boolean` prop — renders an orange "New" badge when true
- Comments in the Comments tab are sorted so new ones appear first

---

## Feature 2 — Reply Detection & "Fix with comment" Button

### Goal

When someone replies to a comment we posted, we see it highlighted and can trigger a fix that incorporates the full discussion thread.

### Data model changes

**`Comment`** domain model gains two fields:

```python
class Comment:
    ...
    in_reply_to_id: int | None   # from GitHub API field of the same name
    thread_id: int               # root comment ID of this thread (computed)
    is_ours: bool                # author == our_github_login
```

`thread_id` is computed server-side: walk the `in_reply_to_id` chain until a comment with no parent is found; that root ID becomes the `thread_id` for all comments in the chain.

### Thread reconstruction logic

```python
def build_threads(comments: list[Comment]) -> dict[int, list[Comment]]:
    # returns {root_id: [root, reply1, reply2, ...]}
```

A reply is flagged as `is_new_reply = True` when:
- `root_comment.is_ours == True`
- `reply.is_ours == False`
- `reply.created_at > last_visited_at`

`is_new_reply` is added to the `Comment` response model.

### Backend changes

**`GitHubCLIAdapter.get_pr_comments()`** (existing):
- Maps the GitHub API `in_reply_to_id` field onto the `Comment` model
- Note: only review inline comments (`/pulls/{pr}/comments`) have `in_reply_to_id`. PR-level issue comments (`/issues/{pr}/comments`) do not support threading — they are treated as standalone and `in_reply_to_id` defaults to `None`.

**`CommentService`** (existing) gains `build_threads()` and thread-enrichment logic.

**`GET /api/pr/{owner}/{repo}/{pr_number}`** response adds:
- `in_reply_to_id`, `thread_id`, `is_ours`, `is_new_reply` on each comment

### "Fix with comment" button

Appears on a `CommentCard` when **all** of:
- `comment.is_ours == True`
- the thread has at least one reply (`thread_id` maps to > 1 comment)
- at least one reply has `is_new_reply == True`

The button label: `Fix with comment` (replaces the existing `Fix` button for this card).

**`POST /api/comment/fix`** (existing endpoint) gains an optional field:

```python
class ImplementFix(BaseModel):
    ...
    thread: list[dict] | None = None
    # ordered list of {"author": str, "body": str} dicts representing the thread
```

When `thread` is provided, `OpenCodeAdapter.stream_fix()` builds a richer prompt:

```
You are implementing a fix for a code review finding.

Original comment:
{our original comment body}

Discussion that followed:
- {reply 1 author}: {reply 1 body}
- {our reply if any}: {body}
- {reply 2 author}: {body}

Implement the fix taking the full discussion into account.
```

### Frontend changes

- `CommentCard` reads `is_ours`, `thread` (all comments in the thread), and `is_new_reply`
- Shows `💬 N reply` badge in orange if `is_new_reply`, grey otherwise
- Swaps Fix → "Fix with comment" and passes `thread` payload to the fix API call

---

## Feature 3 — Recap Tab

### Goal

A dedicated tab giving an instant summary of all new activity on the PR since last visit.

### Tab placement

Third tab in PR detail view: `Review | Comments | Recap (N)`

The badge count `N = new_comment_ids.length + new_replies.length`. It disappears after the PR is refreshed (i.e., `last_visited_at` is updated).

### Tab content (two sections, chronological desc within each)

**Nouveaux commentaires reçus**
Comments where `comment.id ∈ new_comment_ids AND NOT comment.is_ours`. Each row:
- Author avatar initial + login
- Body truncated to 2 lines
- `file:line` label if inline, empty if PR-level
- Relative timestamp ("il y a 2h")
- Click → scrolls to the card in the Comments tab

**Réponses à nos commentaires**
Comments where `is_new_reply == True`. Same row format, plus:
- "Fix with comment" button directly on the row (no need to navigate to Comments tab)

### Data source

No new API endpoint. The Recap tab is computed entirely from fields already present in the `GET /api/pr/...` response: `new_comment_ids`, `is_ours`, `is_new_reply`. It is a pure frontend view.

### Empty state

When there are no new comments and no new replies: "Tout est à jour — aucune activité depuis ta dernière visite."

---

## Architecture Impact Summary

| Layer | Changes |
|---|---|
| Domain model | `Comment` gains `in_reply_to_id`, `thread_id`, `is_ours`, `is_new_reply` |
| Cache | `last_visited_at` per PR, `github_login.json` global |
| `GitHubCLIAdapter` | Map `in_reply_to_id`; add `get_authenticated_user()` |
| `CommentService` | Add `build_threads()`, new-reply detection |
| `ImplementFix` schema | Optional `thread: list[str]` field |
| `OpenCodeAdapter` | Thread-aware fix prompt when `thread` provided |
| `GET /api/pr/...` | Returns `new_comment_ids`, enriched comments |
| Frontend | Toast, `isNew` badges, `CommentCard` swap, `RecapTab` component |

No new API endpoints required. All changes are additive.

---

## Out of Scope

- Threaded display within the Comments tab (replies remain flat-listed, linked by `thread_id`)
- Notifications outside the app (email, OS notifications)
- Tracking read/unread state beyond `last_visited_at` (per-comment granularity)
- Support for PR-level comment threading (GitHub does not expose `in_reply_to_id` for issue comments)
