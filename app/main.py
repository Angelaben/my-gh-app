"""FastAPI backend for gh-review-tool."""

import json
import logging
import os
import time

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app import cache, gh, opencode

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="gh-review-tool")


# --- Models ---

class RepoAdd(BaseModel):
    owner: str
    name: str


class RepoRemove(BaseModel):
    full_name: str


class PublishComment(BaseModel):
    repo: str
    pr_number: int
    body: str


class ImplementFix(BaseModel):
    repo: str
    pr_number: int
    comment_body: str


# --- Repo endpoints ---

@app.get("/api/repos")
def list_repos():
    return cache.get_repos()


@app.post("/api/repos")
def add_repo(data: RepoAdd):
    return cache.add_repo(data.owner, data.name)


@app.delete("/api/repos")
def remove_repo(data: RepoRemove):
    return cache.remove_repo(data.full_name)


@app.get("/api/repos/search")
def search_repos(org: str, q: str = ""):
    try:
        return gh.search_repos(org, q)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- PR endpoints ---

@app.get("/api/prs/{owner}/{repo}")
def list_prs(owner: str, repo: str):
    full_name = f"{owner}/{repo}"
    return cache.get_prs(full_name)


@app.post("/api/prs/{owner}/{repo}/refresh")
def refresh_prs(owner: str, repo: str):
    full_name = f"{owner}/{repo}"
    try:
        prs = gh.list_prs(full_name)
        cache.save_prs(full_name, prs)
        return prs
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- PR detail ---

@app.get("/api/pr/{owner}/{repo}/{pr_number}")
def get_pr_detail(owner: str, repo: str, pr_number: int):
    full_name = f"{owner}/{repo}"
    try:
        return gh.get_pr_comments(full_name, pr_number)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Review mode ---

@app.post("/api/review/{owner}/{repo}/{pr_number}")
async def run_review(owner: str, repo: str, pr_number: int):
    full_name = f"{owner}/{repo}"
    # Check cache first
    cached = cache.get_review(full_name, pr_number)
    if cached:
        return cached
    try:
        diff = gh.get_pr_diff(full_name, pr_number)
        review = await opencode.run_review(full_name, pr_number, diff)
        cache.save_review(full_name, pr_number, review)
        return review
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/review/{owner}/{repo}/{pr_number}/rerun")
async def rerun_review(owner: str, repo: str, pr_number: int):
    full_name = f"{owner}/{repo}"
    try:
        diff = gh.get_pr_diff(full_name, pr_number)
        review = await opencode.run_review(full_name, pr_number, diff)
        cache.save_review(full_name, pr_number, review)
        return review
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/review/{owner}/{repo}/{pr_number}/stream")
async def stream_review(owner: str, repo: str, pr_number: int):
    """SSE endpoint that streams opencode output in real-time, then emits the parsed result."""
    full_name = f"{owner}/{repo}"

    try:
        diff = gh.get_pr_diff(full_name, pr_number)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    async def event_stream():
        full_output = []
        async for chunk in opencode.stream_review(full_name, pr_number, diff):
            full_output.append(chunk)
            # Send each chunk as an SSE event
            # Escape newlines for SSE format (each line needs "data: " prefix)
            for line in chunk.splitlines(keepends=True):
                escaped = json.dumps({"type": "chunk", "text": line})
                yield f"data: {escaped}\n\n"

        # Once done, parse the full output and send the structured result
        output_text = "".join(full_output).strip()
        review = opencode.parse_review_output(output_text)
        cache.save_review(full_name, pr_number, review)
        yield f"data: {json.dumps({'type': 'result', 'review': review})}\n\n"
        yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# --- Comment actions ---

@app.post("/api/comment/publish")
def publish_comment(data: PublishComment):
    try:
        prefixed = data.body
        gh.post_comment(data.repo, data.pr_number, prefixed)
        return {"status": "published"}
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/comments/{owner}/{repo}/{pr_number}/analyze")
async def analyze_comments(owner: str, repo: str, pr_number: int):
    full_name = f"{owner}/{repo}"
    try:
        pr_data = gh.get_pr_comments(full_name, pr_number)
        comments = pr_data.get("comments", [])
        # Also include review bodies
        for review in pr_data.get("reviews", []):
            if review.get("body"):
                comments.append(review)
        # Include inline review comments (code-level)
        for rc in pr_data.get("review_comments", []):
            rc["_inline"] = True
            comments.append(rc)
        analysis = await opencode.analyze_comments(full_name, pr_number, comments)
        return {"comments": comments, "analysis": analysis}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/comment/fix")
async def implement_fix(data: ImplementFix):
    """SSE endpoint: clone repo, checkout PR branch, run opencode to fix, show diff.
    Does NOT push or create PR — leaves the clone for the user to review."""

    def _sse(event: dict) -> str:
        return f"data: {json.dumps(event)}\n\n"

    async def event_stream():
        try:
            # Step 1: Get PR branch info
            yield _sse({"type": "status", "text": "Fetching PR info..."})
            pr_branch = gh.get_pr_head_branch(data.repo, data.pr_number)
            logger.info("PR #%s head branch: %s", data.pr_number, pr_branch)

            # Step 2: Clone (full clone, NOT temp — user needs to access it)
            # Use a persistent directory under ~/.gh-review-tool/clones/
            clone_base = os.path.expanduser("~/.gh-review-tool/clones")
            os.makedirs(clone_base, exist_ok=True)
            repo_slug = data.repo.replace("/", "_")
            clone_path = os.path.join(clone_base, f"{repo_slug}_pr{data.pr_number}_{int(time.time())}")

            yield _sse({"type": "status", "text": f"Cloning {data.repo}..."})
            gh.clone_repo(data.repo, clone_path)

            # Step 3: Checkout PR branch
            yield _sse({"type": "status", "text": f"Checking out {pr_branch}..."})
            gh.checkout_pr_branch(clone_path, pr_branch)
            yield _sse({"type": "status", "text": f"On branch {pr_branch}"})

            # Step 4: Run opencode in the clone
            yield _sse({"type": "status", "text": "Running opencode to implement fix..."})
            full_output: list[str] = []
            async for chunk in opencode.stream_fix_in_repo(
                clone_path, data.repo, data.pr_number, data.comment_body
            ):
                full_output.append(chunk)
                for line in chunk.splitlines(keepends=True):
                    yield _sse({"type": "chunk", "text": line})

            # Step 5: Check if opencode made changes
            if not gh.has_changes(clone_path):
                yield _sse({"type": "error", "text": "opencode did not make any file changes."})
                yield _sse({"type": "result", "clone_path": clone_path, "has_changes": False, "diff": "", "text": "".join(full_output).strip()})
            else:
                diff_text = gh.get_diff(clone_path)
                yield _sse({"type": "status", "text": f"Changes ready in {clone_path}"})
                yield _sse({
                    "type": "result",
                    "clone_path": clone_path,
                    "branch": pr_branch,
                    "has_changes": True,
                    "diff": diff_text[:10000],
                    "text": "".join(full_output).strip(),
                })

            yield _sse({"type": "done"})

        except Exception as e:
            logger.exception("Fix flow failed")
            yield _sse({"type": "error", "text": str(e)})
            yield _sse({"type": "done"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.delete("/api/review/{owner}/{repo}/{pr_number}/cache")
def clear_review_cache(owner: str, repo: str, pr_number: int):
    full_name = f"{owner}/{repo}"
    path = cache._review_path(full_name, pr_number)
    if path.exists():
        path.unlink()
    return {"status": "cleared"}


# --- Static files ---

app.mount("/", StaticFiles(directory="static", html=True), name="static")
