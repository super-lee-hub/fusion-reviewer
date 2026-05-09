"""Quote verification against normalized document page index — no LLM/API calls."""

from __future__ import annotations

import re
from typing import Literal

from .models import EvidenceRef, Finding

QuoteMatchType = Literal["exact", "normalized", "page_line_only", "not_found"]


def _normalize_text(text: str) -> str:
    """Normalize text for fuzzy comparison.

    Handles: whitespace collapse, newline→space, hyphenated word breaks,
    common Chinese/English punctuation differences.
    """
    # Collapse all whitespace to single space
    text = re.sub(r"\s+", " ", text)
    # Remove soft hyphens and zero-width spaces
    text = text.replace("\xad", "").replace("\u200b", "")
    # Normalize hyphenated line breaks: "word-\nword" → "wordword"
    text = re.sub(r"(\w)-\s+(\w)", r"\1\2", text)
    # Normalize Chinese/English punctuation differences
    text = text.replace("\u201c", "\u201c").replace("\u201d", "\u201d")  # smart quotes
    text = text.replace("\uff0c", ",").replace("\uff0e", ".")  # fullwidth comma/period
    text = text.replace("\u3001", ",").replace("\u3002", ".")  # Chinese comma/period
    text = text.replace("\uff1b", ";").replace("\uff1a", ":")  # fullwidth semicolon/colon
    # Strip and collapse
    return text.strip()


def _get_page_lines(page_index: dict[int, list[str]], page: int) -> list[str] | None:
    if not page_index:
        return None
    return page_index.get(page)


def verify_quote(
    quote: str,
    page_index: dict[int, list[str]],
    page: int | None,
    start_line: int | None,
    end_line: int | None,
) -> QuoteMatchType:
    """Verify a quote against the normalized document page index.

    Returns the match type:
    - ``exact``: verbatim match in the expected page/line range
    - ``normalized``: match after whitespace/punctuation normalization
    - ``page_line_only``: page and line reference valid but quote text not found
    - ``not_found``: quote text not found anywhere in the document
    """
    if not quote or not quote.strip():
        return "not_found"

    # Try exact match in expected location
    if page is not None and page in page_index:
        lines = page_index[page]
        if start_line is not None:
            line_start = max(0, start_line - 1)
            line_end = min(len(lines), (end_line or start_line))
            context = " ".join(lines[line_start:line_end])
            if quote.strip() in context:
                return "exact"
            if _normalize_text(quote) in _normalize_text(context):
                return "normalized"

    # Try exact match anywhere in the document
    for page_lines in page_index.values():
        full_text = " ".join(page_lines)
        if quote.strip() in full_text:
            if page is not None:
                return "normalized"  # found but not at expected location
            return "exact"

    # Try normalized match anywhere
    normalized_quote = _normalize_text(quote)
    if normalized_quote:
        for page_lines in page_index.values():
            if normalized_quote in _normalize_text(" ".join(page_lines)):
                return "normalized"

    # Page/line reference exists but quote not found
    if page is not None and page in page_index:
        return "page_line_only"

    return "not_found"


def verify_evidence_refs(
    findings: list[Finding],
    page_index: dict[int, list[str]],
) -> list[dict]:
    """Verify evidence refs for all findings. Returns verification results."""
    results: list[dict] = []
    for finding in findings:
        for ref in finding.evidence_refs:
            match_type = verify_quote(
                ref.quote,
                page_index,
                ref.page,
                ref.start_line,
                ref.end_line,
            )
            results.append({
                "finding_id": finding.id,
                "finding_title": finding.title,
                "page": ref.page,
                "start_line": ref.start_line,
                "end_line": ref.end_line,
                "quote_preview": (ref.quote or "")[:80],
                "match_type": match_type,
            })
    return results


def validate_revision_assessment_evidence(
    status: str,
    evidence_refs: list[EvidenceRef],
) -> tuple[bool, str]:
    """Check that an 'addressed' revision assessment has manuscript evidence.

    Returns (is_valid, downgraded_status_or_reason).
    """
    if status == "addressed":
        valid_refs = [r for r in evidence_refs if r.quote or r.page is not None]
        if not valid_refs:
            return False, "partially_addressed"
    return True, ""
