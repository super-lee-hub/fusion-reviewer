"""Deterministic concern merging — no LLM/API calls."""

from __future__ import annotations

import re

from .models import AgentReview, Concern, EvidenceRef

SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4}


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")


def canonical_issue_key(issue_key: str | None, title: str, category: str) -> str:
    if issue_key and slugify(issue_key):
        return slugify(issue_key)
    token = slugify(title)
    prefix = slugify(category or "general")
    return f"{prefix}_{token}" if token else f"{prefix}_issue"


def merge_concerns(reviews: list[AgentReview]) -> list[Concern]:
    merged: list[Concern] = []
    for review in reviews:
        if review.status != "completed":
            continue
        for finding in review.findings:
            target: Concern | None = None
            for concern in merged:
                if concern.issue_key == finding.issue_key:
                    target = concern
                    break
                concern_title = (concern.title or "").strip()
                finding_title = (finding.title or "").strip()
                concern_title_slug = slugify(concern_title)
                finding_title_slug = slugify(finding_title)
                same_title = concern_title == finding_title
                same_title_slug = bool(concern_title_slug and finding_title_slug) and concern_title_slug == finding_title_slug
                compatible_category = concern.category == finding.category
                if compatible_category and (same_title or same_title_slug):
                    target = concern
                    break
            if target is None:
                target = Concern(
                    id=finding.issue_key or slugify(finding.title) or finding.id,
                    issue_key=finding.issue_key or slugify(finding.title) or finding.id,
                    title=finding.title,
                    description=finding.description,
                    category=finding.category,
                    severity=finding.severity,
                    evidence_refs=list(finding.evidence_refs),
                    raised_by=[review.agent_id],
                    specialist_flags=[finding.category] if review.kind == "specialist" else [],
                    needs_external_verification=finding.needs_external_verification,
                    consensus_state="single-source",
                )
                merged.append(target)
                continue
            if review.agent_id not in target.raised_by:
                target.raised_by.append(review.agent_id)
            if target.category == "general" and finding.category != "general":
                target.category = finding.category
            if not target.description and finding.description:
                target.description = finding.description
            for ref in finding.evidence_refs:
                if ref not in target.evidence_refs:
                    target.evidence_refs.append(ref)
            if review.kind == "specialist" and finding.category not in target.specialist_flags:
                target.specialist_flags.append(finding.category)
            if finding.needs_external_verification:
                target.needs_external_verification = True
            if SEVERITY_ORDER.get(finding.severity, 2) > SEVERITY_ORDER.get(target.severity, 2):
                target.severity = finding.severity
    for concern in merged:
        concern.consensus_state = "consensus" if len(concern.raised_by) >= 2 else "single-source"
    return sorted(
        merged,
        key=lambda item: (
            -SEVERITY_ORDER.get(item.severity, 0),
            -len(item.raised_by),
            item.category,
            item.title.lower(),
        ),
    )
