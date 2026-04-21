"""Wrapper around the opencode CLI for AI-powered analysis."""

import asyncio
import json
import logging
import os
import tempfile

logger = logging.getLogger(__name__)


def _clean_env() -> dict[str, str]:
    """Return env without GITHUB_TOKEN so gh calls inside opencode work."""
    env = os.environ.copy()
    env.pop("GITHUB_TOKEN", None)
    env.pop("GH_TOKEN", None)
    return env


async def _run_opencode(message: str, context: str | None = None, timeout: int = 300) -> str:
    """Run opencode with a message. Optionally attach a context file for large content."""
    args = ["opencode", "run"]

    context_file = None
    if context:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False)
        f.write(context)
        f.close()
        context_file = f.name
        args.extend(["--file", context_file])

    args.append(message)

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_clean_env(),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        stdout_str = stdout.decode().strip()
        stderr_str = stderr.decode().strip()
        logger.info("opencode command: %s", " ".join(args))
        logger.info("opencode exit code: %s", proc.returncode)
        logger.info("opencode stdout (%d chars): %s", len(stdout_str), stdout_str[:2000])
        if stderr_str:
            logger.warning("opencode stderr: %s", stderr_str[:2000])
        return stdout_str
    finally:
        if context_file:
            os.unlink(context_file)


async def run_review(repo_full_name: str, pr_number: int, diff: str) -> dict:
    """Run opencode to review a PR diff. Returns structured findings."""
    message = f"""You are reviewing Pull Request #{pr_number} from repository {repo_full_name}.
Analyze the attached diff file and provide a code review. For each issue found, classify it with a criticality level:
- P0: Critical - Security vulnerability, data loss, crash
- P1: Major - Bug, incorrect logic, performance issue
- P2: Minor - Code style, naming, minor improvement
- P3: Suggestion - Nice-to-have, optional improvement

Return your response as a JSON object with this exact structure:
{{"summary": "Brief overall assessment", "findings": [{{"criticality": "P0", "title": "Short title", "description": "Detailed explanation", "file": "filename if applicable", "line": "line number or range if applicable", "suggestion": "Suggested fix if applicable"}}]}}

IMPORTANT: Return ONLY the JSON object, no markdown fences, no extra text."""

    output = await _run_opencode(message, context=diff[:30000])

    # Try to parse JSON from output
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


async def implement_fix(repo_full_name: str, pr_number: int, comment_body: str) -> str:
    """Ask opencode to implement a fix for a comment."""
    message = f"Implement a fix for this code review comment on PR #{pr_number} in {repo_full_name}. The comment is attached. Clone the repo if needed, checkout the PR branch, implement the fix, and describe what you did."

    return await _run_opencode(message, context=comment_body, timeout=600)
