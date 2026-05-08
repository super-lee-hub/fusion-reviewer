"""Revision response review module.

Compares author revision notes against reviewer concerns to evaluate whether
the revision substantively addresses prior feedback.

Follows the classifier.py pattern: accepts a provider + pre-decoded text,
returns a typed dataclass. Does NOT import from higher-level orchestration modules.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .providers import BaseProvider
from .text_utils import looks_garbled

MAX_REVISION_TEXT_CHARS = 16_000


@dataclass
class RevisionResponseReview:
    """Structured result of a revision-response review."""

    revision_notes_present: bool
    revision_notes_quality: str  # "good" | "garbled" | "missing" | "truncated"
    concerns_addressed: list[str] = field(default_factory=list)
    concerns_partially_addressed: list[str] = field(default_factory=list)
    concerns_ignored: list[str] = field(default_factory=list)
    new_concerns: list[str] = field(default_factory=list)
    overall_assessment: str = ""
    confidence: str = "medium"  # "high" | "medium" | "low"
    markdown: str = ""
    json_payload: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)


def _bullet_list(items: list[str]) -> str:
    if not items:
        return ""
    return "\n".join(f"- {item}" for item in items)


def _limitation_markdown(reason: str) -> str:
    reason_labels = {
        "missing": "未提供返修说明",
        "garbled": "返修说明存在编码问题/乱码",
    }
    label = reason_labels.get(reason, reason)
    return (
        f"## 返修回应审稿\n\n"
        f"⚠️ {label}，无法完成返修回应审稿。\n"
    )


def _build_revision_review_prompt(
    revision_text: str,
    concerns: list,  # list[Concern]
    previous_review_markdown: str | None,
    title: str,
) -> str:
    if concerns:
        concern_lines = []
        for c in concerns:
            cid = getattr(c, "id", getattr(c, "issue_key", "?"))
            severity = getattr(c, "severity", "?")
            ctitle = getattr(c, "title", "")
            desc = getattr(c, "description", "")
            concern_lines.append(f"- [{cid}] ({severity}) {ctitle}: {desc}")
        concern_text = "\n".join(concern_lines)
    else:
        concern_text = "（本次审稿未产生任何 concern）"

    previous_block = ""
    if previous_review_markdown:
        truncated = previous_review_markdown[:8000]
        previous_block = f"## 前次审稿意见\n\n{truncated}\n\n"

    return f"""You are evaluating an author's revision response for the paper "{title}".

Your task: Compare the author's revision notes against the concerns raised during review, and judge whether each concern was substantively addressed.

## Rules
1. Only mark a concern as "addressed" if the revision notes contain a specific, identifiable description of what was changed in response to that concern.
2. Mark as "partially_addressed" if the author acknowledged the concern but the response is vague or incomplete.
3. Mark as "ignored" if the concern is not mentioned at all in the revision notes.
4. Flag any new issues introduced by the revision as "new_concerns".
5. If uncertain, prefer "partially_addressed" or "ignored" over "addressed".
6. Provide an overall_assessment (1-2 paragraphs in Chinese) summarizing whether the revision is adequate.
7. Assign a confidence level: "high" (clear evidence for all judgments), "medium" (some ambiguity), "low" (significant uncertainty).

{previous_block}
## Reviewer Concerns
{concern_text}

## Author Revision Notes
{revision_text}

Return valid JSON only:
{{
  "concerns_addressed": ["concern_id or short label for each addressed concern"],
  "concerns_partially_addressed": ["..."],
  "concerns_ignored": ["..."],
  "new_concerns": ["any new issues introduced by the revision"],
  "overall_assessment": "1-2 paragraph assessment in Chinese",
  "confidence": "high|medium|low"
}}
""".strip()


def _parse_revision_response(payload: str) -> dict:
    """Parse LLM JSON output with regex fallback for unparseable responses."""
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        # Attempt to extract JSON from markdown code block
        match = re.search(r"\{[\s\S]*\}", payload)
        if match:
            return json.loads(match.group())
        raise ValueError(f"Unparseable revision review output: {payload[:200]}")


def review_revision_response(
    *,
    provider: BaseProvider,
    revision_text: str,
    concerns: list,  # list[Concern]
    previous_review_markdown: str | None = None,
    title: str = "",
) -> RevisionResponseReview:
    """Evaluate an author's revision response against reviewer concerns.

    Follows the classifier.py pattern: accepts pre-decoded text (not file paths)
    and a provider, returns a structured dataclass.

    Args:
        provider: An LLM provider with generate(prompt=...) -> ProviderOutput.
        revision_text: Pre-decoded revision notes / response letter text.
        concerns: List of Concern objects from the committee review.
        previous_review_markdown: Optional markdown from a prior review run.
        title: Paper title for context.

    Returns:
        RevisionResponseReview with quality assessment and structured comparison.
    """
    # --- Quality checks (before LLM call, no cost) ---
    if not revision_text or not revision_text.strip():
        return RevisionResponseReview(
            revision_notes_present=False,
            revision_notes_quality="missing",
            overall_assessment="未提供返修说明，无法进行返修回应审稿。",
            markdown=_limitation_markdown("missing"),
            json_payload={"error": "revision_notes_missing"},
        )

    if looks_garbled(revision_text):
        return RevisionResponseReview(
            revision_notes_present=True,
            revision_notes_quality="garbled",
            overall_assessment="返修说明存在编码问题/乱码，无法可靠评估。",
            markdown=_limitation_markdown("garbled"),
            json_payload={"error": "revision_notes_garbled"},
        )

    truncated = False
    if len(revision_text) > MAX_REVISION_TEXT_CHARS:
        revision_text = revision_text[:MAX_REVISION_TEXT_CHARS]
        truncated = True

    # --- Early return when no material to compare ---
    if not concerns and not previous_review_markdown:
        return RevisionResponseReview(
            revision_notes_present=True,
            revision_notes_quality="good",
            overall_assessment="无审稿意见可供对照，无法评估返修回应。",
            markdown="## 返修回应审稿\n\n无审稿意见可供对照，无法评估返修回应。\n",
            json_payload={"error": "no_concerns_to_compare"},
        )

    # --- LLM call ---
    prompt = _build_revision_review_prompt(
        revision_text=revision_text,
        concerns=concerns,
        previous_review_markdown=previous_review_markdown,
        title=title,
    )
    result = provider.generate(prompt=prompt)

    try:
        data = _parse_revision_response(result.payload)
    except Exception:
        return RevisionResponseReview(
            revision_notes_present=True,
            revision_notes_quality="good" if not truncated else "truncated",
            overall_assessment="返修回应审稿 LLM 输出无法解析。",
            markdown="## 返修回应审稿\n\n⚠️ 返修回应审稿 LLM 返回了无法解析的输出，请手动检查。\n",
            json_payload={"error": "unparseable_llm_output", "raw": result.payload[:500]},
            usage=result.usage,
        )

    quality_label = "truncated" if truncated else "good"

    markdown = f"""## 返修回应审稿

**返修说明质量:** {quality_label}
**整体评估:** {data.get("overall_assessment", "")}
**置信度:** {data.get("confidence", "medium")}

### 已回应的问题
{_bullet_list(data.get("concerns_addressed", [])) or "（无）"}

### 部分回应的问题
{_bullet_list(data.get("concerns_partially_addressed", [])) or "（无）"}

### 未回应的问题
{_bullet_list(data.get("concerns_ignored", [])) or "（无）"}

### 修稿引入的新问题
{_bullet_list(data.get("new_concerns", [])) or "（无）"}
"""

    return RevisionResponseReview(
        revision_notes_present=True,
        revision_notes_quality=quality_label,
        concerns_addressed=data.get("concerns_addressed", []),
        concerns_partially_addressed=data.get("concerns_partially_addressed", []),
        concerns_ignored=data.get("concerns_ignored", []),
        new_concerns=data.get("new_concerns", []),
        overall_assessment=data.get("overall_assessment", ""),
        confidence=data.get("confidence", "medium"),
        markdown=markdown,
        json_payload=data,
        usage=result.usage,
    )
