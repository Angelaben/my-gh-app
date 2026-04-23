"""OpenCode CLI implementation of AIProvider."""
import asyncio
import json
import logging
import os
from collections.abc import AsyncGenerator

from app.adapters._subprocess import clean_env
from app.domain.exceptions import ProviderError
from app.domain.models import Comment, Finding, Review
from app.ports.ai_provider import (
    AIProvider,
    FixChunkEvent,
    ReviewChunkEvent,
    ReviewResultEvent,
    ReviewStreamEvent,
)

logger = logging.getLogger(__name__)

_REVIEW_PROMPT_TEMPLATE = """You are reviewing Pull Request #{pr_number} from repository {repo_full_name}.
Analyze the attached diff file and provide a code review. For each issue found, classify it with a criticality level:
- P0: Critical - Security vulnerability, data loss, crash
- P1: Major - Bug, incorrect logic, performance issue
- P2: Minor - Code style, naming, minor improvement
- P3: Suggestion - Nice-to-have, optional improvement

Return your response as a JSON object with this exact structure:
{{"summary": "Brief overall assessment", "findings": [{{"criticality": "P0", "title": "Short title", "description": "Detailed explanation", "file": "filename if applicable", "line": "line number or range if applicable", "suggestion": "Suggested fix if applicable"}}]}}

IMPORTANT: Return ONLY the JSON object, no markdown fences, no extra text."""

_FIX_PROMPT_TEMPLATE = """You are fixing a code review comment on PR #{pr_number} in {repo_full_name}.
The reviewer left this comment:

{comment_body}

You are currently in the repository checkout on the PR branch.
Read the relevant files, understand the issue, and EDIT the files to implement the fix.
Make minimal, targeted changes. Do NOT create new files unless absolutely necessary.
Do NOT run tests or build commands — just make the code changes."""

_OPENCODE_PERMISSIONS = {
    "permissions": {
        "allow": ["Bash", "Edit", "Write", "Read", "Grep", "Glob",
                  "bash", "edit", "write", "read", "grep", "glob"],
    }
}

_OPENCODE_PROJECT_CONFIG = {
    "$schema": "https://opencode.ai/config.json",
    "agent": {
        "build": {
            "permission": {
                "bash": "allow",
                "edit": "allow",
                "write": "allow",
            }
        }
    },
}


def _parse_review_output(output: str) -> Review:
    """Parse raw AI output into a Review. Returns a fallback Review on parse failure."""
    try:
        start = output.find("{")
        end = output.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(output[start:end])
            findings = [
                Finding(
                    priority=f.get("priority", f.get("criticality", "P3")),
                    title=f.get("title", ""),
                    description=f.get("description", ""),
                    file=f.get("file"),
                    line=int(f["line"]) if f.get("line") is not None and str(f["line"]).isdigit() else None,
                    suggestion=f.get("suggestion"),
                )
                for f in data.get("findings", [])
            ]
            return Review(summary=data.get("summary", ""), findings=findings)
    except (json.JSONDecodeError, ValueError):
        pass

    return Review(
        summary="Review completed but output could not be parsed as structured JSON.",
        findings=[],
        raw_output=output,
        raw_length=len(output),
    )


async def _stream_opencode(
    message: str,
    context: str | None = None,
    timeout: int = 300,
    cwd: str | None = None,
) -> AsyncGenerator[str, None]:
    """Stream raw opencode output line by line."""
    prompt = message
    if context:
        prompt = f"{message}\n\n---\n\n{context}"

    extra_args: list[str] = []
    if cwd:
        extra_args = ["--dir", cwd]

    if len(prompt) > 4000:
        proc = await asyncio.create_subprocess_exec(
            "opencode", "run", *extra_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=clean_env(),
        )
        if proc.stdin is not None:
            proc.stdin.write(prompt.encode())
            proc.stdin.close()
    else:
        proc = await asyncio.create_subprocess_exec(
            "opencode", "run", *extra_args, prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=clean_env(),
        )

    if proc.stdout is None or proc.stderr is None:
        raise ProviderError("opencode subprocess did not open stdout/stderr")

    async def _read_stderr() -> list[str]:
        lines = []
        while True:
            line = await proc.stderr.readline()  # type: ignore[union-attr]
            if not line:
                break
            decoded = line.decode().rstrip()
            logger.warning("[opencode stderr] %s", decoded)
            lines.append(decoded)
        return lines

    stderr_task = asyncio.create_task(_read_stderr())

    while True:
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            yield "\n[TIMEOUT]\n"
            break
        if not line:
            break
        decoded = line.decode()
        logger.info("[opencode] %s", decoded.rstrip())
        yield decoded

    await proc.wait()
    logger.info("opencode exit code: %s", proc.returncode)

    noise = ["[STALE]", "fatal: options '--name-only'", "cannot be used together"]
    stderr_lines = await stderr_task
    filtered = [ln for ln in stderr_lines if not any(p in ln for p in noise)]
    if filtered:
        yield "\n--- stderr ---\n"
        for err_line in filtered:
            yield err_line + "\n"


class OpenCodeAdapter(AIProvider):
    """Implements AIProvider using the opencode CLI tool."""

    async def stream_review(
        self, repo_full_name: str, pr_number: int, diff: str
    ) -> AsyncGenerator[ReviewStreamEvent, None]:
        message = _REVIEW_PROMPT_TEMPLATE.format(
            pr_number=pr_number, repo_full_name=repo_full_name
        )
        chunks: list[str] = []
        async for chunk in _stream_opencode(message, context=diff[:30000]):
            chunks.append(chunk)
            yield ReviewChunkEvent(text=chunk)

        full_output = "".join(chunks).strip()
        review = _parse_review_output(full_output)
        yield ReviewResultEvent(review=review)

    async def analyze_comments(
        self, repo_full_name: str, pr_number: int, comments: list[Comment]
    ) -> list[dict]:
        if not comments:
            return []

        comments_text = "\n\n".join(
            f"Comment by {c.author}:\n{c.body}" for c in comments
        )
        message = (
            f"Analyze the comments from PR #{pr_number} in {repo_full_name} attached in the file.\n"
            "For each comment, assess criticality (P0-P3), validity (true/false), interest (high/medium/low), "
            "and provide a summary.\n"
            'Return a JSON array: [{"author": "username", "criticality": "P0", "valid": true, '
            '"interest": "high", "summary": "Brief analysis", "original_body": "first 100 chars"}]\n'
            "IMPORTANT: Return ONLY the JSON array, no markdown fences, no extra text."
        )

        output_parts: list[str] = []
        async for chunk in _stream_opencode(message, context=comments_text[:20000]):
            output_parts.append(chunk)
        output = "".join(output_parts)

        try:
            start = output.find("[")
            end = output.rfind("]") + 1
            if start >= 0 and end > start:
                return json.loads(output[start:end])
        except json.JSONDecodeError:
            pass
        return []

    async def stream_fix(
        self, repo_dir: str, repo_full_name: str, pr_number: int, comment_body: str
    ) -> AsyncGenerator[FixChunkEvent, None]:
        self._write_opencode_config(repo_dir)

        prompt = _FIX_PROMPT_TEMPLATE.format(
            pr_number=pr_number,
            repo_full_name=repo_full_name,
            comment_body=comment_body,
        )
        async for chunk in _stream_opencode(prompt, cwd=repo_dir, timeout=600):
            yield FixChunkEvent(text=chunk)

    @staticmethod
    def _write_opencode_config(repo_dir: str) -> None:
        """Write opencode permission config files to the repo directory."""
        settings_dir = os.path.join(repo_dir, ".opencode")
        os.makedirs(settings_dir, exist_ok=True)
        with open(os.path.join(settings_dir, "settings.json"), "w") as f:
            json.dump(_OPENCODE_PERMISSIONS, f)

        config_path = os.path.join(repo_dir, "opencode.json")
        with open(config_path, "w") as f:
            json.dump(_OPENCODE_PROJECT_CONFIG, f, indent=2)
        logger.info("Wrote opencode.json at %s", config_path)

    async def generate_text(self, prompt: str, timeout: int = 60) -> str:
        parts: list[str] = []
        async for chunk in _stream_opencode(prompt, timeout=timeout):
            parts.append(chunk)
        result = "".join(parts).strip().strip("`").strip()
        if result and len(result) <= 500:
            return result
        raise ProviderError(f"generate_text returned unusable output (length={len(result)})")
