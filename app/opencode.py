"""Wrapper around the opencode CLI for AI-powered analysis."""

import asyncio
import json
import logging
import os
import tempfile
from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)


def _clean_env() -> dict[str, str]:
    """Return env without GITHUB_TOKEN so gh calls inside opencode work."""
    env = os.environ.copy()
    env.pop("GITHUB_TOKEN", None)
    env.pop("GH_TOKEN", None)
    return env


async def _run_opencode(message: str, context: str | None = None, timeout: int = 300) -> str:
    """Run opencode with a message. If context is provided, it's appended to the message in a temp file."""
    output_parts: list[str] = []
    async for chunk in _stream_opencode(message, context, timeout):
        output_parts.append(chunk)
    return "".join(output_parts)


async def _stream_opencode(
    message: str, context: str | None = None, timeout: int = 300, cwd: str | None = None,
) -> AsyncGenerator[str, None]:
    """Stream opencode output line by line as it runs."""
    prompt = message
    if context:
        prompt = f"{message}\n\n---\n\n{context}"

    context_file = None
    if len(prompt) > 4000:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False)
        f.write(prompt)
        f.close()
        context_file = f.name

    extra_args = []
    if cwd:
        extra_args = ["--dir", cwd]

    try:
        if context_file:
            proc = await asyncio.create_subprocess_exec(
                "opencode", "run", *extra_args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_clean_env(),
            )
            with open(context_file) as fh:
                input_bytes = fh.read().encode()
            assert proc.stdin is not None
            proc.stdin.write(input_bytes)
            proc.stdin.close()
        else:
            proc = await asyncio.create_subprocess_exec(
                "opencode", "run", *extra_args, prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_clean_env(),
            )

        assert proc.stdout is not None
        assert proc.stderr is not None

        # Read stdout and stderr concurrently
        async def _read_stderr():
            stderr_lines = []
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                decoded = line.decode().rstrip()
                logger.warning("[opencode stderr] %s", decoded)
                stderr_lines.append(decoded)
            return stderr_lines

        stderr_task = asyncio.create_task(_read_stderr())

        # Read stdout line by line as it streams
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

        # Yield stderr as well so the frontend can see it
        stderr_lines = await stderr_task
        if stderr_lines:
            yield "\n--- stderr ---\n"
            for err_line in stderr_lines:
                yield err_line + "\n"

    finally:
        if context_file:
            os.unlink(context_file)


def _build_review_prompt(repo_full_name: str, pr_number: int) -> str:
    return f"""You are reviewing Pull Request #{pr_number} from repository {repo_full_name}.
Analyze the attached diff file and provide a code review. For each issue found, classify it with a criticality level:
- P0: Critical - Security vulnerability, data loss, crash
- P1: Major - Bug, incorrect logic, performance issue
- P2: Minor - Code style, naming, minor improvement
- P3: Suggestion - Nice-to-have, optional improvement

Return your response as a JSON object with this exact structure:
{{"summary": "Brief overall assessment", "findings": [{{"criticality": "P0", "title": "Short title", "description": "Detailed explanation", "file": "filename if applicable", "line": "line number or range if applicable", "suggestion": "Suggested fix if applicable"}}]}}

IMPORTANT: Return ONLY the JSON object, no markdown fences, no extra text."""


async def stream_review(repo_full_name: str, pr_number: int, diff: str) -> AsyncGenerator[str, None]:
    """Stream opencode review output line by line."""
    message = _build_review_prompt(repo_full_name, pr_number)
    async for chunk in _stream_opencode(message, context=diff[:30000]):
        yield chunk


def parse_review_output(output: str) -> dict:
    """Parse the full review output into structured findings."""
    try:
        start = output.find("{")
        end = output.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(output[start:end])
    except json.JSONDecodeError:
        pass

    return {
        "summary": "Review completed but output could not be parsed as structured JSON.",
        "raw_output": output,
        "raw_length": len(output),
        "findings": [],
    }


async def run_review(repo_full_name: str, pr_number: int, diff: str) -> dict:
    """Run opencode to review a PR diff. Returns structured findings."""
    message = _build_review_prompt(repo_full_name, pr_number)
    output = await _run_opencode(message, context=diff[:30000])
    return parse_review_output(output)


async def analyze_comments(repo_full_name: str, pr_number: int, comments: list[dict]) -> list[dict]:
    """Analyze PR comments for criticality, validity, and interest."""
    if not comments:
        return []

    comments_text = "\n\n".join(
        f"Comment by {c.get('author', {}).get('login', 'unknown')}:\n{c.get('body', '')}"
        for c in comments
    )

    message = f"""Analyze the comments from PR #{pr_number} in {repo_full_name} attached in the file.
For each comment, assess criticality (P0-P3), validity (true/false), interest (high/medium/low), and provide a summary.
Return a JSON array: [{{"author": "username", "criticality": "P0", "valid": true, "interest": "high", "summary": "Brief analysis", "original_body": "first 100 chars"}}]
IMPORTANT: Return ONLY the JSON array, no markdown fences, no extra text."""

    output = await _run_opencode(message, context=comments_text[:20000])

    try:
        start = output.find("[")
        end = output.rfind("]") + 1
        if start >= 0 and end > start:
            return json.loads(output[start:end])
    except json.JSONDecodeError:
        pass

    return []


async def stream_fix_in_repo(
    repo_dir: str, repo_full_name: str, pr_number: int, comment_body: str,
) -> AsyncGenerator[str, None]:
    """Run opencode in a cloned repo directory to implement a fix. Streams output."""
    # Write .opencode/settings.json to allow all tools in this temp clone
    settings_dir = os.path.join(repo_dir, ".opencode")
    os.makedirs(settings_dir, exist_ok=True)
    settings = {
        "permissions": {
            "allow": ["Bash", "Edit", "Write", "Read", "Grep", "Glob",
                       "bash", "edit", "write", "read", "grep", "glob"],
        }
    }
    with open(os.path.join(settings_dir, "settings.json"), "w") as f:
        json.dump(settings, f)

    # Also write opencode.json at project root with permission overrides
    project_config = {
        "agent": {
            "build": {
                "permission": {
                    "bash": "allow",
                    "edit": "allow",
                    "write": "allow",
                }
            }
        }
    }
    with open(os.path.join(repo_dir, "opencode.json"), "w") as f:
        json.dump(project_config, f)

    prompt = f"""You are fixing a code review comment on PR #{pr_number} in {repo_full_name}.
The reviewer left this comment:

{comment_body}

You are currently in the repository checkout on the PR branch.
Read the relevant files, understand the issue, and EDIT the files to implement the fix.
Make minimal, targeted changes. Do NOT create new files unless absolutely necessary.
Do NOT run tests or build commands — just make the code changes."""

    async for chunk in _stream_opencode(prompt, cwd=repo_dir, timeout=600):
        yield chunk
