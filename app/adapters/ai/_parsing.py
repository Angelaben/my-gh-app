"""Output-parsing helpers shared by every CLI-based AI adapter.

Both opencode and claude-code are asked to return strict JSON, but in practice
either one will occasionally wrap the JSON in stray prose or emit a malformed
object. Centralising the parsing keeps the lenient JSON-extraction logic in a
single, testable place.
"""
import json
import logging
import re

from app.domain.models import Finding, Review


def _parse_line(val: object) -> int | None:
    """Extract the first integer from a line reference (handles ranges like '42-50')."""
    if val is None:
        return None
    m = re.match(r'\d+', str(val).strip())
    return int(m.group()) if m else None


def parse_review_output(output: str, *, provider: str) -> Review:
    """Parse raw AI output into a Review.

    Returns a fallback Review (with the raw output preserved) when the JSON
    cannot be located or decoded so the UI can still surface what the model
    produced.
    """
    logger = logging.getLogger(provider)
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
                    line=_parse_line(f.get("line")),
                    suggestion=f.get("suggestion"),
                )
                for f in data.get("findings", [])
            ]
            review = Review(summary=data.get("summary", ""), findings=findings)
            logger.info("%s | parsed review | findings=%d", provider, len(findings))
            return review
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning(
            "%s | parse failed | output_chars=%d error=%s", provider, len(output), exc
        )

    logger.warning("%s | returning fallback review | output_chars=%d", provider, len(output))
    return Review(
        summary="Review completed but output could not be parsed as structured JSON.",
        findings=[],
        raw_output=output,
        raw_length=len(output),
    )


def parse_analyze_output(output: str) -> list[dict]:
    """Parse raw AI output into a comment-analysis list.

    Returns ``[]`` when the JSON array can't be located or decoded.
    """
    try:
        start = output.find("[")
        end = output.rfind("]") + 1
        if start >= 0 and end > start:
            return json.loads(output[start:end])
    except json.JSONDecodeError:
        pass
    return []
