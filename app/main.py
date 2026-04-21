"""FastAPI backend for gh-review-tool."""

import logging

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import cache, gh, opencode

logging.basicConfig(level=logging.INFO)

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


# --- Comment actions ---

@app.post("/api/comment/publish")
def publish_comment(data: PublishComment):
    try:
        prefixed = f"- Opencode review -\n\n{data.body}"
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
    try:
        result = await opencode.implement_fix(data.repo, data.pr_number, data.comment_body)
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/review/{owner}/{repo}/{pr_number}/cache")
def clear_review_cache(owner: str, repo: str, pr_number: int):
    full_name = f"{owner}/{repo}"
    path = cache._review_path(full_name, pr_number)
    if path.exists():
        path.unlink()
    return {"status": "cleared"}


# --- Static files ---

app.mount("/", StaticFiles(directory="static", html=True), name="static")
