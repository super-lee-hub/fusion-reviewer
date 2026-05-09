"""Quote verification against normalized document page index — no LLM/API calls."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from .models import EvidenceRef, Finding

if TYPE_CHECKING:
    from .models import AgentReview, RevisionAssessment

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


class EvidenceValidationSummary(BaseModel):
    """Per-reviewer summary of evidence validation results."""
    total_refs: int = 0
    exact_matches: int = 0
    normalized_matches: int = 0
    page_line_only: int = 0
    not_found: int = 0
    invalid_rate: float = 0.0
    evidence_unreliable: bool = False
    downgraded_findings: list[str] = Field(default_factory=list)
    excluded_findings: list[str] = Field(default_factory=list)


def validate_review_evidence(
    review: AgentReview,
    page_index: dict[int, list[str]],
) -> tuple[AgentReview, EvidenceValidationSummary]:
    """Validate all evidence refs in a review's findings against the page_index.

    Applies downgrade/exclusion rules:
    - exact/normalized: valid, enters concern merge
    - page_line_only: retained but weak; critical severity downgraded to high
    - not_found: excluded from concern merge, written to diagnostics

    Returns (filtered_review, validation_summary).
    """
    summary = EvidenceValidationSummary()
    valid_findings: list[Finding] = []

    for finding in review.findings:
        ref_results = []
        for ref in finding.evidence_refs:
            summary.total_refs += 1
            match_type = verify_quote(
                ref.quote, page_index, ref.page, ref.start_line, ref.end_line
            )
            ref_results.append(match_type)
            if match_type == "exact":
                summary.exact_matches += 1
            elif match_type == "normalized":
                summary.normalized_matches += 1
            elif match_type == "page_line_only":
                summary.page_line_only += 1
            else:
                summary.not_found += 1

        # Determine finding disposition
        all_not_found = all(m == "not_found" for m in ref_results) if ref_results else True

        if all_not_found and finding.evidence_refs:
            # Finding has evidence refs but none found — exclude from merge
            summary.excluded_findings.append(finding.id)
            continue

        if any(m == "page_line_only" for m in ref_results):
            # Downgrade critical to high
            if finding.severity == "critical":
                finding = finding.model_copy(update={"severity": "high"})
                summary.downgraded_findings.append(finding.id)

        valid_findings.append(finding)

    # Compute invalid rate and unreliable flag
    if summary.total_refs > 0:
        summary.invalid_rate = summary.not_found / summary.total_refs
    if summary.invalid_rate >= 0.5 and len(review.findings) >= 2:
        summary.evidence_unreliable = True

    filtered_review = review.model_copy(update={"findings": valid_findings})
    return filtered_review, summary


def validate_revision_assessment_evidence(
    assessment: "RevisionAssessment",
    page_index: dict[int, list[str]] | None = None,
) -> tuple[bool, str]:
    """Check that an 'addressed' revision assessment has valid manuscript evidence.

    For ``status=addressed``, at least one ``manuscript_evidence_refs[]`` must have
    an exact or normalized match in the revised manuscript's page_index.

    Returns (is_valid, downgraded_status_or_reason).
    """
    if assessment.status == "addressed":
        refs = assessment.manuscript_evidence_refs
        if not refs:
            return False, "partially_addressed"
        if page_index is not None:
            has_valid = False
            for ref in refs:
                match_type = verify_quote(
                    ref.quote, page_index, ref.page, ref.start_line, ref.end_line
                )
                if match_type in ("exact", "normalized"):
                    has_valid = True
                    break
            if not has_valid:
                # Check if any page/line ref exists but quote doesn't match
                has_page_ref = any(r.page is not None for r in refs)
                if has_page_ref:
                    return False, "partially_addressed"
                return False, "unclear"
        else:
            # Without page_index, at least require non-empty refs
            valid_refs = [r for r in refs if r.quote or r.page is not None]
            if not valid_refs:
                return False, "partially_addressed"
    return True, ""
