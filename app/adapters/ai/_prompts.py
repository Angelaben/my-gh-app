"""Prompt templates shared by every CLI-based AI adapter.

Keeping prompts in one place ensures the JSON contracts the parsers rely on
stay in lockstep across providers — when we refine the schema we only update
it once and every adapter benefits.

These templates are passed through ``str.format`` with a small fixed set of
named placeholders (``pr_number``, ``repo_full_name``, ``comment_body``).
**Any literal ``{`` or ``}`` in the template body must be doubled** (`{{` /
`}}`) — see the JSON examples below. Subclasses that override these
attributes need to follow the same rule or the format call will raise
``KeyError`` at runtime.
"""

REVIEW_PROMPT = """You are reviewing Pull Request #{pr_number} from repository {repo_full_name}.
Analyze the attached diff and provide a code review. For each issue found, classify it with a criticality level:
- P0: Critical - Security vulnerability, data loss, crash
- P1: Major - Bug, incorrect logic, performance issue
- P2: Minor - Code style, naming, minor improvement
- P3: Suggestion - Nice-to-have, optional improvement

Return your response as a JSON object with this exact structure:
{{"summary": "Brief overall assessment", "findings": [{{"criticality": "P0", "confidence": "high | medium | low — how confident you are this is a real issue", "title": "Short title", "description": "Detailed explanation", "file": "path/to/file.py or null", "line": "line number or range (e.g. 42 or 42-50) — required when file is not null, otherwise null", "suggestion": "Suggested fix if applicable"}}]}}

Report every issue you find, including ones you are uncertain about or consider low-severity. Do not self-filter for importance — use the criticality and confidence fields so findings can be ranked and filtered downstream.

IMPORTANT: Return ONLY the JSON object, no markdown fences, no extra text."""


SYNTHESIZE_PROMPT = """You are consolidating the code review of Pull Request #{pr_number} from repository {repo_full_name}.
The PR diff was too large for a single pass, so it was split into parts that were reviewed independently and in parallel. Attached are the complete list of files changed by the PR, the summary each sub-review produced, and every candidate finding they reported (JSON).

Produce the final consolidated review:
- Merge duplicates: the same underlying issue reported by different parts (possibly with different wording, criticality, or nearby lines) must become ONE finding. Keep the most precise file/line, the clearest description, and the highest criticality among the duplicates.
- Re-rank criticality consistently across the whole PR using the same scale the sub-reviews used (P0 critical, P1 major, P2 minor, P3 suggestion).
- Do NOT drop any distinct issue and do NOT invent new findings — every non-duplicate candidate must appear in the output.
- Write one coherent overall summary of the PR (not a list of per-part summaries): what the PR does, the main risks, and the themes across findings.

Return your response as a JSON object with this exact structure:
{{"summary": "Coherent overall assessment", "findings": [{{"criticality": "P0", "confidence": "high | medium | low", "title": "Short title", "description": "Detailed explanation", "file": "path/to/file.py or null", "line": "line number or range or null", "suggestion": "Suggested fix if applicable"}}]}}

IMPORTANT: Return ONLY the JSON object, no markdown fences, no extra text."""


FIX_PROMPT = """You are fixing a code review comment on PR #{pr_number} in {repo_full_name}.
The reviewer left this comment:

{comment_body}

You are currently in the repository checkout on the PR branch.
Read the relevant files, understand the issue, and EDIT the files to implement the fix.
Make minimal, targeted changes. Do NOT create new files unless absolutely necessary.
Do NOT run tests or build commands — just make the code changes."""


ANALYZE_PROMPT = (
    "Analyze the comments from PR #{pr_number} in {repo_full_name} attached below.\n"
    "Each comment is tagged with a numeric id ([id=N]).\n"
    "For each comment, assess criticality (P0-P3), validity (true/false), interest "
    "(high/medium/low), and provide a summary.\n"
    'Return a JSON array: [{{"id": 0, "author": "username", "criticality": "P0", '
    '"valid": true, "interest": "high", "summary": "Brief analysis", '
    '"original_body": "first 100 chars"}}]\n'
    "The id field MUST echo the [id=N] tag of the comment being analyzed.\n"
    "IMPORTANT: Return ONLY the JSON array, no markdown fences, no extra text."
)
