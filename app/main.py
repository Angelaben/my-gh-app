"""FastAPI backend — thin controllers wired to services via dependency injection."""

import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from collections.abc import AsyncGenerator
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.adapters.ai.claude_code_adapter import ClaudeCodeAdapter
from app.adapters.ai.opencode_adapter import OpenCodeAdapter
from app.adapters.cache.json_file_cache import JsonFileCache
from app.adapters.vcs.github_cli_adapter import GitHubCLIAdapter
from app.adapters.worktree.git_worktree_adapter import GitWorktreeAdapter
from app.domain.exceptions import WorktreeNoChangesError, WorktreeNotFoundError
from app.domain.models import Comment
from app.ports.ai_provider import (
    AIProvider,
    FixChunkEvent,
    ReviewChunkEvent,
    ReviewProgressEvent,
    ReviewResultEvent,
    ReviewStreamEvent,
    ReviewWarningEvent,
)
from app.request_context import request_id_var
from app.services import metrics as metrics_store
from app.services import settings_store
from app.services._diff_splitter import extract_hunk_rows, split_unified_diff
from app.services.comment_service import CommentService
from app.services.fix_service import FixService
from app.services.review_service import ReviewService

_LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
logging.basicConfig(
    level=getattr(logging, _LOG_LEVEL, logging.INFO),
    format="%(levelname)s %(name)s [%(request_id)s] %(message)s",
)

from app import log_buffer, log_setup  # noqa: E402  — must come after basicConfig
if log_setup.LOG_OPS_ENABLED:
    log_setup.setup()

# In-memory ring buffer backing GET /api/logs/* (Activity page). Installed
# before the request-id filter loop below so it gets the filter too.
_log_ring = log_buffer.install()


class _RequestIdFilter(logging.Filter):
    """Stamp every record with the current request's correlation id (or '-')."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        return True


for _handler in logging.getLogger().handlers:
    _handler.addFilter(_RequestIdFilter())

logger = logging.getLogger(__name__)

app = FastAPI(title="gh-review-tool")


@app.middleware("http")
async def _log_http(request: Request, call_next):
    t0 = time.monotonic()
    response = await call_next(request)
    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "http | %s %s → %d (%.0f ms)",
        request.method, request.url.path, response.status_code, elapsed_ms,
    )
    return response


@app.middleware("http")
async def _assign_request_id(request: Request, call_next):
    # Registered after _log_http → outermost middleware, so the id is set
    # before any request logging. The contextvar is intentionally never
    # reset — see app.request_context for why (SSE generators).
    request_id = uuid.uuid4().hex[:8]
    request_id_var.set(request_id)
    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    return response

# --- Provider registry & availability ---

# CLI binary expected on PATH for each provider, in display order.
_PROVIDER_CLIS: dict[str, str] = {"opencode": "opencode", "claude-code": "claude"}
_SUPPORTED_PROVIDERS: tuple[str, ...] = tuple(_PROVIDER_CLIS.keys())


def _provider_available(name: str) -> bool:
    """True if the provider's CLI binary is on PATH."""
    cli = _PROVIDER_CLIS.get(name)
    return cli is not None and shutil.which(cli) is not None


def _build_ai_provider(name: str) -> AIProvider:
    """Instantiate the AI provider adapter selected by name."""
    if name == "claude-code":
        return ClaudeCodeAdapter()
    if name == "opencode":
        return OpenCodeAdapter()
    raise ValueError(
        f"Unknown AI_PROVIDER {name!r}. Supported: {', '.join(_SUPPORTED_PROVIDERS)}"
    )


class SwitchableAIProvider(AIProvider):
    """Delegates to one of several AI provider adapters, chosen at runtime.

    The active provider can be changed via :meth:`set_active` without
    rebuilding the services that hold a reference to this wrapper.
    """

    def __init__(self, providers: dict[str, AIProvider], active: str) -> None:
        if active not in providers:
            raise ValueError(f"Unknown active provider {active!r}")
        self._providers = providers
        self._active = active

    @property
    def active(self) -> str:
        return self._active

    def set_active(self, name: str) -> None:
        if name not in self._providers:
            raise ValueError(
                f"Unknown provider {name!r}. Supported: {', '.join(self._providers)}"
            )
        self._active = name

    def _delegate(self) -> AIProvider:
        return self._providers[self._active]

    async def stream_review(
        self,
        repo_full_name: str,
        pr_number: int,
        diff: str,
        model: str | None = None,
        timeout: int = 300,
    ) -> AsyncGenerator[ReviewStreamEvent, None]:
        async for event in self._delegate().stream_review(
            repo_full_name, pr_number, diff, model=model, timeout=timeout
        ):
            yield event

    async def analyze_comments(
        self, repo_full_name: str, pr_number: int, comments: list[Comment]
    ) -> list[dict]:
        return await self._delegate().analyze_comments(repo_full_name, pr_number, comments)

    async def stream_fix(
        self, repo_dir: str, repo_full_name: str, pr_number: int, comment_body: str
    ) -> AsyncGenerator[FixChunkEvent, None]:
        async for chunk in self._delegate().stream_fix(
            repo_dir, repo_full_name, pr_number, comment_body
        ):
            yield chunk

    async def generate_text(self, prompt: str, timeout: int = 60) -> str:
        return await self._delegate().generate_text(prompt, timeout=timeout)


def _resolve_initial_provider() -> tuple[str, bool]:
    """Pick the initial active provider.

    Returns ``(name, from_env)``. Resolution order:

    1. ``AI_PROVIDER`` env var if set and supported (warn if its CLI is missing).
    2. First provider whose CLI is on PATH.
    3. ``opencode`` as last-resort fallback (so the app still boots and the
       user can pick a provider from the UI).
    """
    raw = os.environ.get("AI_PROVIDER", "").strip().lower()
    if raw:
        if raw not in _SUPPORTED_PROVIDERS:
            logger.warning(
                "AI_PROVIDER=%r is not supported (expected one of %s); falling back to auto-detect.",
                raw, ", ".join(_SUPPORTED_PROVIDERS),
            )
        else:
            if not _provider_available(raw):
                logger.warning(
                    "AI_PROVIDER=%r is set, but the %r CLI is not on PATH. "
                    "Reviews will fail until you install it or switch providers via the UI.",
                    raw, _PROVIDER_CLIS[raw],
                )
            return raw, True

    for name in _SUPPORTED_PROVIDERS:
        if _provider_available(name):
            return name, False

    logger.warning(
        "No supported AI provider CLI found on PATH (looked for %s). "
        "The server will boot but reviews will fail until a CLI is installed.",
        ", ".join(_PROVIDER_CLIS.values()),
    )
    return _SUPPORTED_PROVIDERS[0], False


_initial_provider, _provider_from_env = _resolve_initial_provider()

_cache = JsonFileCache()
_vcs = GitHubCLIAdapter()
_worktree = GitWorktreeAdapter()
_ai = SwitchableAIProvider(
    providers={name: _build_ai_provider(name) for name in _SUPPORTED_PROVIDERS},
    active=_initial_provider,
)
logger.info(
    "AI provider | active=%s | from_env=%s | available=%s",
    _ai.active, _provider_from_env,
    {name: _provider_available(name) for name in _SUPPORTED_PROVIDERS},
)

_review_service = ReviewService(ai=_ai, cache=_cache, vcs=_vcs)
_fix_service = FixService(ai=_ai, vcs=_vcs, worktree=_worktree)
_comment_service = CommentService(ai=_ai, vcs=_vcs)


def get_review_service() -> ReviewService:
    return _review_service


def get_fix_service() -> FixService:
    return _fix_service


def get_comment_service() -> CommentService:
    return _comment_service


# --- Request / Response models ---

class RepoAdd(BaseModel):
    owner: str
    name: str


class RepoRemove(BaseModel):
    full_name: str


class PublishComment(BaseModel):
    repo: str
    pr_number: int
    body: str


class DeleteComment(BaseModel):
    repo: str
    comment_id: int
    comment_type: str  # 'pr_comment' | 'review_comment'


class PublishInlineComment(BaseModel):
    repo: str
    pr_number: int
    body: str
    path: str
    line: int


class ReviewCommentItem(BaseModel):
    path: str
    line: int
    body: str


class PublishReview(BaseModel):
    repo: str
    pr_number: int
    body: str
    event: str = "REQUEST_CHANGES"  # REQUEST_CHANGES | APPROVE | COMMENT
    comments: list[ReviewCommentItem] = []


class ImplementFix(BaseModel):
    repo: str
    pr_number: int
    comment_body: str
    thread: list[dict] | None = None  # [{"author": str, "body": str}, ...]


class PushFix(BaseModel):
    repo: str
    pr_number: int
    diff: str
    comment_body: str
    branch: str


# --- Repo endpoints ---

@app.get("/api/repos")
def list_repos():
    return _cache.get_repos()


@app.post("/api/repos")
def add_repo(data: RepoAdd):
    return _cache.add_repo(data.owner, data.name)


@app.delete("/api/repos")
def remove_repo(data: RepoRemove):
    return _cache.remove_repo(data.full_name)


@app.get("/api/repos/search")
def search_repos(org: str, q: str = ""):
    try:
        return _vcs.search_repos(org, q)
    except Exception as e:
        logger.exception("search_repos failed | org=%s q=%s", org, q)
        raise HTTPException(status_code=500, detail=str(e)) from e


# --- Config & provider endpoints ---


def _provider_status() -> dict:
    """Snapshot of provider availability + active selection."""
    return {
        "active": _ai.active,
        "from_env": _provider_from_env,
        "supported": list(_SUPPORTED_PROVIDERS),
        "available": {name: _provider_available(name) for name in _SUPPORTED_PROVIDERS},
        "clis": dict(_PROVIDER_CLIS),
    }


@app.get("/api/config")
def get_config():
    """Return static runtime configuration consumed by the frontend.

    Includes ``ai_provider`` for backwards compatibility with older frontend
    builds; new frontends should prefer ``GET /api/providers``.
    """
    status = _provider_status()
    return {
        "ai_provider": status["active"],
        "supported_providers": status["supported"],
        "providers": status,
    }


@app.get("/api/providers")
def get_providers():
    """Return active provider, env-var origin, and per-provider availability."""
    return _provider_status()


class ProviderSelect(BaseModel):
    name: str


@app.post("/api/provider")
def set_provider(data: ProviderSelect):
    """Switch the active AI provider at runtime.

    Returns 400 if ``name`` isn't a supported provider, and warns (via the
    response body) if the provider's CLI is not on PATH — the call still
    succeeds so the user can install the CLI without restarting.
    """
    name = data.name.strip().lower()
    if name not in _SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider {name!r}. Supported: {', '.join(_SUPPORTED_PROVIDERS)}",
        )
    _ai.set_active(name)
    available = _provider_available(name)
    if not available:
        logger.warning(
            "Switched active provider to %r but %r CLI is missing on PATH.",
            name, _PROVIDER_CLIS[name],
        )
    else:
        logger.info("AI provider switched | active=%s", name)
    return {**_provider_status(), "warning": None if available else (
        f"Provider {name!r} selected, but its CLI ({_PROVIDER_CLIS[name]!r}) is not on PATH."
    )}


@app.get("/api/models")
def list_models():
    """Return all models available in the active AI provider's installation."""
    import subprocess
    active = _ai.active
    if active == "opencode":
        cmd = ["opencode", "models"]
    elif active == "claude-code":
        from app.adapters.ai.claude_code_adapter import list_models as _claude_list_models
        return {"models": _claude_list_models()}
    else:
        raise HTTPException(status_code=500, detail=f"Unknown provider {active!r}")

    if not _provider_available(active):
        return {"models": [], "warning": f"{_PROVIDER_CLIS[active]!r} CLI not found on PATH."}

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10,
        )
        models = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return {"models": models}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# --- Settings & prompts endpoints ---


class SettingsUpdate(BaseModel):
    review_timeout: int | None = None
    review_diff_max_chars: int | None = None
    review_max_concurrency: int | None = None
    review_synthesis: bool | None = None
    review_ignore_globs: list[str] | None = None


class PromptUpdate(BaseModel):
    body: str


@app.get("/api/settings")
def get_settings():
    """Effective review settings plus their built-in defaults."""
    return {
        "review": settings_store.get_review_settings(),
        "defaults": settings_store.default_review_settings(),
    }


@app.post("/api/settings")
def update_settings(data: SettingsUpdate):
    try:
        return {"review": settings_store.save_review_settings(data.model_dump(exclude_none=True))}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/settings/reset")
def reset_settings():
    return {"review": settings_store.reset_review_settings()}


@app.get("/api/prompts")
def get_prompts():
    """All AI prompts with their defaults and whether a custom override is active."""
    return settings_store.get_prompts()


@app.post("/api/prompts/{name}")
def update_prompt(name: str, data: PromptUpdate):
    try:
        return settings_store.save_prompt(name, data.body)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=f"Unknown prompt {name!r}") from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.delete("/api/prompts/{name}")
def reset_prompt(name: str):
    try:
        return settings_store.reset_prompt(name)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=f"Unknown prompt {name!r}") from e


# --- PR endpoints ---

@app.get("/api/prs/{owner}/{repo}")
def list_prs(owner: str, repo: str):
    return _cache.get_prs(f"{owner}/{repo}")


@app.post("/api/prs/{owner}/{repo}/refresh")
def refresh_prs(owner: str, repo: str):
    full_name = f"{owner}/{repo}"
    logger.info("refresh_prs | start | repo=%s", full_name)
    try:
        prs = _vcs.list_prs(full_name)
        prs_dicts = [
            {
                "number": pr.number,
                "title": pr.title,
                "author": pr.author,
                "branch": pr.branch,
                "base_branch": pr.base_branch,
                "additions": pr.additions,
                "deletions": pr.deletions,
                "updated_at": pr.updated_at,
                "url": pr.url,
                "is_draft": pr.is_draft,
            }
            for pr in prs
        ]
        _cache.save_prs(full_name, prs_dicts)
        logger.info("refresh_prs | done | repo=%s prs=%d", full_name, len(prs_dicts))
        return prs_dicts
    except Exception as e:
        logger.exception("refresh_prs | failed | repo=%s", full_name)
        raise HTTPException(status_code=500, detail=str(e)) from e


# --- PR detail ---

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
        logger.exception("get_pr_detail | failed | repo=%s/%s pr=#%d", owner, repo, pr_number)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/pr/{owner}/{repo}/{pr_number}/hunk")
def get_pr_hunk(owner: str, repo: str, pr_number: int, path: str, line: int, context: int = 4):
    """Return diff rows around a finding's line for the inline mini-diff view."""
    try:
        repo_full_name = f"{owner}/{repo}"
        raw_diff = _vcs.get_diff(repo_full_name, pr_number)
        file_section = next(
            (f for f in split_unified_diff(raw_diff) if f.path == path), None
        )
        if file_section is None:
            return {"found": False, "path": path, "target_line": line, "rows": []}
        rows, found = extract_hunk_rows(file_section.content, line, max(0, min(context, 20)))
        return {
            "found": found,
            "path": path,
            "target_line": line,
            "rows": [
                {"sign": r.sign, "old_line": r.old_line, "new_line": r.new_line, "text": r.text}
                for r in rows
            ],
        }
    except Exception as e:
        logger.exception(
            "get_pr_hunk | failed | repo=%s/%s pr=#%d path=%s", owner, repo, pr_number, path
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


def _serialize_review(review) -> dict:
    """Serialize a Review domain object to a JSON-compatible dict."""
    result: dict = {
        "summary": review.summary,
        "findings": [
            {
                "priority": f.priority,
                "title": f.title,
                "description": f.description,
                "file": f.file,
                "line": f.line,
                "suggestion": f.suggestion,
                "confidence": f.confidence,
            }
            for f in review.findings
        ],
    }
    if review.raw_output:
        result["raw_output"] = review.raw_output
    if review.raw_length:
        result["raw_length"] = review.raw_length
    if review.head_sha:
        result["head_sha"] = review.head_sha
    if review.created_at:
        result["created_at"] = review.created_at
    return result


# --- Review endpoints ---

@app.post("/api/review/{owner}/{repo}/{pr_number}")
async def run_review(
    owner: str,
    repo: str,
    pr_number: int,
    svc: ReviewService = Depends(get_review_service),
):
    try:
        full_name = f"{owner}/{repo}"
        review = await svc.get_or_run_review(full_name, pr_number)
        result = _serialize_review(review)
        result["stale"] = svc.is_review_stale(full_name, pr_number, review)
        return result
    except Exception as e:
        logger.exception("run_review | failed | repo=%s/%s pr=#%d", owner, repo, pr_number)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/review/{owner}/{repo}/{pr_number}/rerun")
async def rerun_review(
    owner: str,
    repo: str,
    pr_number: int,
    svc: ReviewService = Depends(get_review_service),
):
    try:
        review = await svc.rerun_review(f"{owner}/{repo}", pr_number)
        return _serialize_review(review)
    except Exception as e:
        logger.exception("rerun_review | failed | repo=%s/%s pr=#%d", owner, repo, pr_number)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/review/{owner}/{repo}/{pr_number}/incremental")
async def incremental_review(
    owner: str,
    repo: str,
    pr_number: int,
    svc: ReviewService = Depends(get_review_service),
):
    """Re-review only the files changed since the cached review's head SHA."""
    try:
        full_name = f"{owner}/{repo}"
        result = await svc.incremental_review(full_name, pr_number)
        payload = _serialize_review(result["review"])
        payload["stale"] = svc.is_review_stale(full_name, pr_number, result["review"])
        payload["incremental"] = {
            "no_changes": result["no_changes"],
            "base_sha": result["base_sha"],
            "head_sha": result["head_sha"],
            "changed_files": result["changed_files"],
            "carried": result["carried"],
            "new": result["new"],
        }
        return payload
    except Exception as e:
        logger.exception("incremental_review | failed | repo=%s/%s pr=#%d", owner, repo, pr_number)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/review/{owner}/{repo}/{pr_number}/stream")
async def stream_review(
    owner: str,
    repo: str,
    pr_number: int,
    model: str | None = Query(default=None, description="AI provider model override (e.g. anthropic/claude-opus-4-5 for opencode, claude-opus-4-7 for claude-code)"),
    timeout: int | None = Query(default=None, ge=30, le=3600, description="Per-line inactivity timeout in seconds (defaults to the saved Settings value)."),
    svc: ReviewService = Depends(get_review_service),
):
    """SSE endpoint that streams AI output in real-time, then emits the parsed result."""
    async def event_stream():
        try:
            # Correlation id first: lets the UI's agent-activity console link
            # this run to the matching backend log records (X-Request-Id).
            yield f"data: {json.dumps({'type': 'meta', 'request_id': request_id_var.get() or ''})}\n\n"
            async for event in svc.stream_review(f"{owner}/{repo}", pr_number, model=model, timeout=timeout):
                if isinstance(event, ReviewChunkEvent):
                    for line in event.text.splitlines(keepends=True):
                        yield f"data: {json.dumps({'type': 'chunk', 'text': line})}\n\n"
                elif isinstance(event, ReviewProgressEvent):
                    yield f"data: {json.dumps({'type': 'progress', 'text': event.text})}\n\n"
                elif isinstance(event, ReviewWarningEvent):
                    yield f"data: {json.dumps({'type': 'warning', 'lines': event.lines})}\n\n"
                elif isinstance(event, ReviewResultEvent):
                    yield f"data: {json.dumps({'type': 'result', 'review': _serialize_review(event.review)})}\n\n"
            yield 'data: {"type": "done"}\n\n'
        except Exception as e:
            logger.exception("stream_review | failed | repo=%s/%s pr=#%d", owner, repo, pr_number)
            # Field name is `message` to match the frontend's SSEReviewEvent
            # contract — `text` was a regression that left the UI showing an
            # empty error box.
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
            yield 'data: {"type": "done"}\n\n'

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# --- Comment endpoints ---

@app.post("/api/comment/publish")
def publish_comment(data: PublishComment, svc: CommentService = Depends(get_comment_service)):
    try:
        svc.post_comment(data.repo, data.pr_number, data.body)
        return {"status": "published"}
    except Exception as e:
        logger.exception("publish_comment | failed | repo=%s pr=#%d", data.repo, data.pr_number)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/comment/inline")
def publish_inline_comment(
    data: PublishInlineComment, svc: CommentService = Depends(get_comment_service)
):
    try:
        svc.post_inline_comment(data.repo, data.pr_number, data.body, data.path, data.line)
        return {"status": "published"}
    except Exception as e:
        logger.exception("publish_inline_comment | failed | repo=%s pr=#%d", data.repo, data.pr_number)
        raise HTTPException(status_code=500, detail=str(e)) from e


_FALLBACK_WARNINGS = {
    "comments_folded_into_body": (
        "GitHub rejected one or more inline comments (line outside the PR "
        "diff). The review was published with the findings folded into the "
        "review body instead."
    ),
    "posted_as_comment": (
        "GitHub refused to create a review (you may be the PR's author, or "
        "the PR is closed). Posted the findings as a regular PR comment "
        "instead — the 'Changes requested' banner won't appear."
    ),
}


@app.post("/api/review/publish")
def publish_review(data: PublishReview, svc: CommentService = Depends(get_comment_service)):
    try:
        result = svc.create_review(
            data.repo,
            data.pr_number,
            data.body,
            data.event,
            [c.model_dump() for c in data.comments],
        )
        response = {"status": "published", "url": result.get("html_url"), "id": result.get("id")}
        fallback = result.get("_fallback_applied")
        if fallback in _FALLBACK_WARNINGS:
            response["warning"] = _FALLBACK_WARNINGS[fallback]
            if result.get("_fallback_reason"):
                response["detail"] = result["_fallback_reason"]
        return response
    except Exception as e:
        logger.exception("publish_review | failed | repo=%s pr=#%d", data.repo, data.pr_number)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.delete("/api/comment")
def delete_comment(data: DeleteComment):
    try:
        _vcs.delete_comment(data.repo, data.comment_id, data.comment_type)
        return {"status": "deleted"}
    except Exception as e:
        logger.exception("delete_comment | failed | repo=%s id=%d", data.repo, data.comment_id)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/comments/{owner}/{repo}/{pr_number}/analyze")
async def analyze_comments(
    owner: str,
    repo: str,
    pr_number: int,
    svc: CommentService = Depends(get_comment_service),
):
    try:
        return await svc.analyze_comments(f"{owner}/{repo}", pr_number)
    except Exception as e:
        logger.exception("analyze_comments | failed | repo=%s/%s pr=#%d", owner, repo, pr_number)
        raise HTTPException(status_code=500, detail=str(e)) from e


# --- Fix endpoints ---

@app.post("/api/comment/fix")
async def implement_fix(
    data: ImplementFix, svc: FixService = Depends(get_fix_service)
):
    async def event_stream():
        async for event in svc.stream_fix(data.repo, data.pr_number, data.comment_body):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.post("/api/comment/fix/push")
async def push_fix(data: PushFix, svc: FixService = Depends(get_fix_service)):
    try:
        return await svc.push_fix(data.repo, data.pr_number, data.diff, data.comment_body)
    except WorktreeNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except WorktreeNoChangesError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("push_fix | failed | repo=%s pr=#%d", data.repo, data.pr_number)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/comment/fix/new-pr")
async def submit_new_pr(data: PushFix, svc: FixService = Depends(get_fix_service)):
    try:
        return await svc.create_pr_from_fix(
            data.repo, data.pr_number, data.branch, data.diff, data.comment_body
        )
    except WorktreeNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except WorktreeNoChangesError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("submit_new_pr | failed | repo=%s pr=#%d", data.repo, data.pr_number)
        raise HTTPException(status_code=500, detail=str(e)) from e


# --- Worktree management ---

@app.get("/api/worktrees")
def list_worktrees():
    return _worktree.list_worktrees()


@app.delete("/api/worktree/{owner}/{repo}/{pr_number}")
def remove_worktree(owner: str, repo: str, pr_number: int):
    try:
        _worktree.remove(f"{owner}/{repo}", pr_number)
        return {"status": "removed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.delete("/api/review/{owner}/{repo}/{pr_number}/cache")
def clear_review_cache(owner: str, repo: str, pr_number: int):
    _review_service.clear_cache(f"{owner}/{repo}", pr_number)
    return {"status": "cleared"}


# --- Stats & logs (Activity page) ---

@app.get("/api/stats")
def get_stats():
    """Aggregated operational metrics (reviews run, durations, findings)."""
    return metrics_store.summarize()


@app.get("/api/stats/history")
def get_stats_history(days: int = Query(default=14, ge=1, le=90)):
    """Per-day review activity for the Activity-page trends section."""
    return metrics_store.history(days)


_STATS_POLL_S = 2.0
_LOGS_POLL_S = 0.5
_SSE_KEEPALIVE_TICKS = 15  # ~30s for stats, ~7.5s for logs


@app.get("/api/stats/stream")
async def stream_stats():
    """SSE: push the aggregated stats whenever a new metrics record lands."""
    async def event_stream():
        # Starts as a value file_token() can never return so the first
        # iteration always emits the current stats snapshot.
        last_token: object = ()
        idle_ticks = 0
        while True:
            token = metrics_store.file_token()
            if token != last_token:
                last_token = token
                idle_ticks = 0
                yield f"data: {json.dumps({'type': 'stats', 'stats': metrics_store.summarize()})}\n\n"
            else:
                idle_ticks += 1
                if idle_ticks >= _SSE_KEEPALIVE_TICKS:
                    idle_ticks = 0
                    yield ": keepalive\n\n"
            await asyncio.sleep(_STATS_POLL_S)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.get("/api/logs/recent")
def get_recent_logs(after_seq: int = 0, limit: int = 200):
    """Recent backend log records from the in-memory ring buffer."""
    records = _log_ring.records_after(after_seq=after_seq, limit=limit)
    return {"records": records, "last_seq": _log_ring.last_seq}


@app.get("/api/logs/stream")
async def stream_logs():
    """SSE: push new backend log records as they are emitted."""
    async def event_stream():
        last_seq = _log_ring.last_seq
        idle_ticks = 0
        while True:
            records = _log_ring.records_after(after_seq=last_seq, limit=200)
            if records:
                last_seq = records[-1]["seq"]
                idle_ticks = 0
                for record in records:
                    yield f"data: {json.dumps({'type': 'log', 'record': record})}\n\n"
            else:
                idle_ticks += 1
                if idle_ticks >= _SSE_KEEPALIVE_TICKS:
                    idle_ticks = 0
                    yield ": keepalive\n\n"
            await asyncio.sleep(_LOGS_POLL_S)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# --- Static files ---

Path("dist").mkdir(exist_ok=True)
app.mount("/", StaticFiles(directory="dist", html=True), name="static")
