from __future__ import annotations

import csv
import re
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from deepreview.report.final_report import validate_final_report
from deepreview.types import JobStatus

from .classifier import classify_manuscript
from .config import (
    AgentSlotConfig,
    ParadigmCriteriaConfig,
    get_settings,
    load_paradigm_criteria,
    load_review_plan,
)
from .evidence import (
    concerns_to_annotations,
    evidence_ref_to_text,
    export_pdf_report,
    prepare_document_once,
    quote_for_span,
    seed_evidence_refs,
    serialize_page_index,
)
from .models import (
    FALLBACK_PARADIGM,
    AgentReview,
    AgentSummary,
    Concern,
    EditorReport,
    EvidenceRef,
    ManuscriptParadigm,
)
from .providers import ProviderRegistry
from .revision_review import RevisionResponseReview, review_revision_response
from .storage import (
    append_event,
    ensure_artifact_paths,
    load_job_state,
    mutate_job_state,
    reviews_dir,
    source_input_path,
    write_json_atomic,
    write_text_atomic,
    write_friendly_artifact_aliases,
)


SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4}
DECISION_VALUES = {"accept", "minor_revision", "major_revision", "reject"}


def _decision_label(value: str) -> str:
    return {
        "accept": "接收",
        "minor_revision": "小修",
        "major_revision": "大修",
        "reject": "拒稿",
    }.get(value, value)


def _severity_label(value: str) -> str:
    return {
        "low": "低",
        "medium": "中",
        "high": "高",
        "critical": "严重",
    }.get(value, value)


def _consensus_state_label(value: str) -> str:
    return {
        "consensus": "多数共识",
        "disagreement": "存在分歧",
        "single-source": "单一来源",
    }.get(value, value)


def _kind_label(value: str) -> str:
    return {
        "generalist": "综合审稿人",
        "specialist": "专项审稿人",
        "editor": "编辑",
    }.get(value, value)


def _review_source_label(value: str) -> str:
    return {
        "subagent": "真实子代理",
        "local": "主线程本地",
        "service": "后端服务",
        "unknown": "未标注来源",
    }.get(value, value)


def _prefer_chinese_output() -> bool:
    return not get_settings().force_english_output


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")


def infer_review_source(review: AgentReview) -> str:
    token = slugify(getattr(review, "review_source", "unknown"))
    if token in {"subagent", "local", "service"}:
        return token
    profile = slugify(getattr(review, "provider_profile", ""))
    if "subagent" in profile:
        return "subagent"
    if any(marker in profile for marker in ("local", "repair", "root", "committee", "skill")):
        return "local"
    if profile:
        return "service"
    return "unknown"


def with_inferred_review_source(review: AgentReview) -> AgentReview:
    source = infer_review_source(review)
    if review.review_source == source:
        return review
    return review.model_copy(update={"review_source": source})


def summarize_review_sources(
    reviews: list[AgentReview],
    *,
    expected_subagent_reviews: int | None = None,
) -> dict[str, int | bool | None]:
    completed = [with_inferred_review_source(item) for item in reviews if item.status == "completed"]
    counts = {"subagent": 0, "local": 0, "service": 0, "unknown": 0}
    for review in completed:
        counts[review.review_source] = counts.get(review.review_source, 0) + 1
    missing = max((expected_subagent_reviews or 0) - counts["subagent"], 0) if expected_subagent_reviews is not None else 0
    return {
        "expected_subagent_reviews": expected_subagent_reviews,
        "completed_subagent_reviews": counts["subagent"],
        "completed_local_reviews": counts["local"],
        "completed_service_reviews": counts["service"],
        "completed_unknown_source_reviews": counts["unknown"],
        "missing_subagent_slots": missing,
        "full_subagent_committee": None if expected_subagent_reviews is None else missing == 0,
    }


def _canonical_issue_key(issue_key: str | None, title: str, category: str) -> str:
    if issue_key and slugify(issue_key):
        return slugify(issue_key)
    token = slugify(title)
    prefix = slugify(category or "general")
    return f"{prefix}_{token}" if token else f"{prefix}_issue"


def _safe_severity(value: Any) -> str:
    token = slugify(str(value or "medium"))
    if token in SEVERITY_ORDER:
        return token
    return "medium"


def _safe_decision(value: Any) -> str:
    token = slugify(str(value or "major_revision"))
    return token if token in DECISION_VALUES else "major_revision"


def _agent_artifact_stem(slot: AgentSlotConfig) -> str:
    if slot.kind == "generalist":
        return f"committee_review_{slot.id}"
    if slot.kind == "specialist":
        return f"specialist_review_{slot.category}"
    return "meta_review"


def _set_status(job_id: str, status: JobStatus, message: str) -> None:
    def apply(job):
        job.status = status
        job.message = message

    mutate_job_state(job_id, apply)
    append_event(job_id, "status", status=status.value, message=message)


def _fail_job(job_id: str, message: str, error: str) -> None:
    def apply(job):
        job.status = JobStatus.failed
        job.message = message
        job.error = error

    mutate_job_state(job_id, apply)
    append_event(job_id, "failed", message=message, error=error)


def _update_usage(job_id: str, usage: dict[str, int]) -> None:
    def apply(job):
        job.usage.token.requests += int(usage.get("requests", 0))
        job.usage.token.input_tokens += int(usage.get("input_tokens", 0))
        job.usage.token.output_tokens += int(usage.get("output_tokens", 0))
        job.usage.token.total_tokens += int(usage.get("total_tokens", 0))

    mutate_job_state(job_id, apply)


def render_agent_markdown(review: AgentReview) -> str:
    review = with_inferred_review_source(review)
    lines = [
        f"# {review.title}",
        "",
        f"- 审稿人 ID：`{review.agent_id}`",
        f"- 类型：`{_kind_label(review.kind)}`",
        f"- 来源：`{_review_source_label(review.review_source)}`",
        f"- Provider 配置：`{review.provider_profile}`",
        f"- 模型：`{review.model}`",
        f"- 审稿建议：`{_decision_label(review.recommendation)}`",
        "",
        "## 摘要意见",
        review.summary or "未记录摘要意见。",
        "",
        "## 优点",
    ]
    lines.extend([f"- {item}" for item in review.strengths] or ["- 未记录明显优点。"])
    lines.extend(["", "## 主要问题"])
    lines.extend([f"- {item}" for item in review.weaknesses] or ["- 未记录明显问题。"])
    lines.extend(["", "## 具体发现"])
    if not review.findings:
        lines.append("- 没有生成结构化问题条目。")
    for finding in review.findings:
        lines.append(f"### {finding.title}")
        lines.append(f"- 问题键：`{finding.issue_key or finding.id}`")
        lines.append(f"- 类别：`{finding.category}`")
        lines.append(f"- 严重程度：`{_severity_label(finding.severity)}`")
        lines.append(f"- 问题说明：{finding.description}")
        if finding.recommendation:
            lines.append(f"- 修改建议：{finding.recommendation}")
        for ref in finding.evidence_refs:
            locator = evidence_ref_to_text(ref)
            quote = ref.quote.replace("\n", " ").strip()
            lines.append(f"- 证据：{locator}: {quote}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def render_editor_markdown(editor: EditorReport) -> str:
    lines = [
        f"# {editor.title}",
        "",
        f"- 最终结论：`{_decision_label(editor.decision)}`",
        f"- Provider 配置：`{editor.provider_profile}`",
        f"- 模型：`{editor.model}`",
        "",
    ]
    if editor.expected_subagent_reviews is not None:
        lines.extend(
            [
                "## 评审来源说明",
                f"- 预期真实 subagent reviewer：`{editor.expected_subagent_reviews}`",
                f"- 已完成真实 subagent reviewer：`{editor.completed_subagent_reviews}`",
                f"- 主线程本地补写 reviewer：`{editor.completed_local_reviews}`",
                f"- 后端服务 reviewer：`{editor.completed_service_reviews}`",
                f"- 未标注来源 reviewer：`{editor.completed_unknown_source_reviews}`",
                f"- 缺失 subagent 槽位：`{editor.missing_subagent_slots}`",
                "",
            ]
        )
    lines.append("## 共识问题")
    lines.extend([f"- {item}" for item in editor.consensus] or ["- 未提炼出稳定共识。"])
    lines.extend(["", "## 主要分歧"])
    lines.extend([f"- {item}" for item in editor.disagreements] or ["- 未提炼出明显分歧。"])
    lines.extend(["", "## 优先修改项"])
    lines.extend([f"- {item}" for item in editor.priority_revisions] or ["- 未提炼出优先修改项。"])
    lines.extend(["", "## 裁决理由", editor.decision_rationale or "未记录裁决理由。"])
    return "\n".join(lines).strip() + "\n"


def _journal_prompt_block(journal_requirements: str | None) -> str:
    if not journal_requirements or not journal_requirements.strip():
        return "No journal-specific requirements were provided."
    return f"""Journal requirements:
{journal_requirements.strip()}

Rules for journal context:
- Distinguish paper-quality issues from journal-fit issues.
- Do not invent journal requirements beyond the text provided.
- If a point is only about journal fit, say so explicitly.
""".strip()


def _language_prompt_block() -> str:
    if _prefer_chinese_output():
        return """Language rules:
- Use Chinese for all user-facing content in summary, strengths, weaknesses, findings, consensus, disagreements, priority revisions, and decision rationale.
- Keep JSON keys, issue_key values, and enum values in ASCII / English as required by the schema.
""".strip()
    return """Language rules:
- Use English for all user-facing content unless the source material requires direct Chinese quotation.
""".strip()


def _paradigm_criteria_block(
    paradigm: ManuscriptParadigm | None,
    criteria: ParadigmCriteriaConfig,
    slot: AgentSlotConfig,
) -> str:
    if paradigm is None or paradigm is FALLBACK_PARADIGM or not paradigm.paradigm_labels:
        lines = [
            "Paradigm-aware review instructions:",
            "This manuscript has not been pre-classified. Review the paper using criteria appropriate to its apparent research paradigm.",
            "If the paper appears to be theoretical, apply theoretical criteria (derivation, assumptions, proof, conceptual logic).",
            "If empirical, apply empirical criteria (identification, data, robustness, measurement).",
            "If review/synthesis, apply synthesis criteria (coverage, method, gap identification).",
            "",
            "General criteria to consider:",
        ]
        lines.extend(f"- {item}" for item in criteria.fallback_focus)
        return "\n".join(lines)

    paradigm_tags = {lb.label for lb in paradigm.paradigm_labels}
    matching = [p for p in criteria.paradigms if p.tag in paradigm_tags]
    if not matching:
        matching = [p for p in criteria.paradigms if p.coarse_family == paradigm.coarse_family]

    family_label = str(paradigm.coarse_family)
    labels_str = ", ".join(
        f"{lb.label} (confidence: {lb.confidence:.0%})" for lb in paradigm.paradigm_labels
    )

    lines = [
        f"Paradigm-aware review instructions:",
        f"This manuscript is classified as **{family_label}** paradigm: {labels_str}.",
        f"Classification rationale: {paradigm.rationale}",
        "",
    ]

    if matching:
        all_focus: list[str] = []
        all_inappropriate: list[str] = []
        for m in matching:
            all_focus.extend(m.appropriate_focus)
            all_inappropriate.extend(m.inappropriate_critique_patterns)
        all_focus = list(dict.fromkeys(all_focus))
        all_inappropriate = list(dict.fromkeys(all_inappropriate))

        lines.append("Appropriate review criteria for this manuscript type:")
        lines.extend(f"- {item}" for item in all_focus[:8])
        lines.append("")
        if all_inappropriate:
            lines.append("AVOID the following critique types (they do not apply to this paradigm):")
            lines.extend(f"- {item}" for item in all_inappropriate[:6])
            lines.append("")

    if slot.category and slot.category != paradigm.coarse_family:
        lines.append(
            f"NOTE: Your specialty category is '{slot.category}', but the manuscript paradigm is '{family_label}'. "
            f"Adapt your review criteria to the manuscript's actual paradigm, not your default specialty."
        )
        lines.append("")

    return "\n".join(lines)


def _build_review_prompt(
    slot: AgentSlotConfig,
    title: str,
    source_name: str,
    indexed_text: str,
    journal_requirements: str | None,
    paradigm: ManuscriptParadigm | None = None,
    paradigm_criteria: ParadigmCriteriaConfig | None = None,
) -> str:
    focus = "\n".join(f"- {item}" for item in slot.focus_areas)
    return f"""
You are {slot.title}.

Review the paper "{title}" ({source_name}) using only the indexed evidence below.
Focus on:
{focus}

Tone:
- {slot.tone_instruction}

{_paradigm_criteria_block(paradigm, paradigm_criteria or load_paradigm_criteria(), slot) if paradigm is not None or paradigm_criteria is not None else ""}

Return valid JSON with this schema:
{{
  "summary": "short summary",
  "strengths": ["..."],
  "weaknesses": ["..."],
  "recommendation": "accept|minor_revision|major_revision|reject",
  "findings": [
    {{
      "issue_key": "stable cross-reviewer issue key",
      "title": "short issue title",
      "description": "concise but specific issue description",
      "category": "{slot.category or 'general'}",
      "severity": "low|medium|high|critical",
      "evidence_refs": [
        {{
          "page": 1,
          "start_line": 1,
          "end_line": 2,
          "quote": "exact quote from indexed evidence"
        }}
      ],
      "needs_external_verification": false,
      "recommendation": "one concrete fix"
    }}
  ]
}}

Rules:
- Every finding must cite at least one evidence ref.
- Quotes must match the indexed evidence exactly.
- Prefer globally stable issue_key values such as "evidence_support_gap" or "method_identification_weakness".
- Keep the output concise and useful for editorial decision-making.

{_language_prompt_block()}

{_journal_prompt_block(journal_requirements)}

Indexed evidence:
{indexed_text}
""".strip()


def _build_editor_prompt(
    title: str,
    concerns: list[Concern],
    reviews: list[AgentReview],
    journal_requirements: str | None,
    paradigm: ManuscriptParadigm | None = None,
) -> str:
    concern_rows = [item.model_dump(mode="json") for item in concerns[:24]]
    review_rows = [
        {
            "agent_id": review.agent_id,
            "title": review.title,
            "recommendation": review.recommendation,
            "summary": review.summary,
        }
        for review in reviews
    ]

    paradigm_block = ""
    if paradigm is not None and paradigm is not FALLBACK_PARADIGM and paradigm.paradigm_labels:
        labels_str = ", ".join(
            f"{lb.label} (confidence: {lb.confidence:.0%})" for lb in paradigm.paradigm_labels
        )
        paradigm_block = f"""
Manuscript paradigm classification:
- Coarse family: {paradigm.coarse_family}
- Paradigm labels: {labels_str}
- Classification rationale: {paradigm.rationale}

Paradigm-aware editor rules:
- Identify any reviewer concern that presupposes a different research paradigm than the classified one (e.g., demanding empirical identification for a theory paper).
- Downgrade or remove paradigm-mismatched concerns from the consensus and priority_revisions lists.
- If a concern category conflicts with the manuscript paradigm, flag it in disagreements rather than consensus.
- Note any paradigm-ambiguity-driven disagreements explicitly.
"""
    elif paradigm is FALLBACK_PARADIGM:
        paradigm_block = """
Manuscript paradigm classification:
- Status: UNCLASSIFIED (classification step failed)
- Reviewers were instructed to apply criteria appropriate to the paper's apparent methodology.
- Be cautious of reviewer concerns that may be rooted in paradigm misapplication.
"""

    journal_paradigm_block = ""
    if journal_requirements and journal_requirements.strip() and paradigm is not None and paradigm is not FALLBACK_PARADIGM and paradigm.paradigm_labels:
        journal_paradigm_block = f"""
Journal-paradigm interaction:
- If the journal requirements imply a paradigm preference (e.g., the journal primarily publishes empirical research) and the manuscript's classified paradigm ({paradigm.coarse_family}) differs, flag this as a potential journal-fit concern.
- Do not downgrade the paper's academic quality because of a paradigm mismatch with the journal — keep paper-quality and journal-fit as separate dimensions.
"""

    return f"""
You are the Meta Review Editor for the paper "{title}".

Use the structured concern list and reviewer summaries to produce valid JSON:
{{
  "decision": "accept|minor_revision|major_revision|reject",
  "consensus": ["..."],
  "disagreements": ["..."],
  "priority_revisions": ["..."],
  "decision_rationale": "concise explanation"
}}

Rules:
- Only include evidence-backed issues in consensus and priority revisions.
- Separate high-confidence committee consensus from single-reviewer disagreements.
- Distinguish manuscript quality problems from journal-fit problems when journal requirements are provided.
{paradigm_block}
{journal_paradigm_block}

{_language_prompt_block()}

{_journal_prompt_block(journal_requirements)}

Reviewer summaries:
{review_rows}

Concerns:
{concern_rows}
""".strip()


def _normalize_ref(raw: dict[str, Any], page_index: dict[int, list[str]]) -> EvidenceRef:
    page = raw.get("page")
    if page is not None:
        page = max(1, int(page or 1))
    start = raw.get("start_line")
    if start is not None:
        start = max(1, int(start or 1))
    end = raw.get("end_line")
    if end is not None:
        end = max(start or 1, int(end or start or 1))
    quote = str(raw.get("quote") or "").strip()
    if not quote and page is not None:
        quote = quote_for_span(page_index, page, start, end)
    locator = str(raw.get("locator") or "").strip() or None
    return EvidenceRef(
        page=page,
        start_line=start,
        end_line=end,
        quote=quote,
        locator=locator,
        image_path=str(raw.get("image_path") or "").strip() or None,
    )


def normalize_review_payload(
    *,
    slot: AgentSlotConfig,
    provider_profile: str,
    model: str,
    page_index: dict[int, list[str]],
    payload: dict[str, Any],
) -> AgentReview:
    findings = []
    for idx, raw in enumerate(payload.get("findings", []), start=1):
        if not isinstance(raw, dict):
            continue
        ref_rows = raw.get("evidence_refs") or raw.get("evidence_spans") or []
        refs = [_normalize_ref(item, page_index) for item in ref_rows if isinstance(item, dict)]
        refs = [item for item in refs if item.quote or item.page is not None or item.image_path]
        if not refs:
            continue
        title = str(raw.get("title") or f"{slot.title} finding {idx}").strip()
        category = str(raw.get("category") or slot.category or "general").strip() or "general"
        issue_key = _canonical_issue_key(str(raw.get("issue_key") or raw.get("id") or ""), title, category)
        findings.append(
            {
                "id": str(raw.get("id") or issue_key or f"{slot.id}_finding_{idx}"),
                "issue_key": issue_key,
                "title": title,
                "description": str(raw.get("description") or "").strip(),
                "category": category,
                "severity": _safe_severity(raw.get("severity")),
                "evidence_refs": [ref.model_dump(mode="json") for ref in refs],
                "needs_external_verification": bool(raw.get("needs_external_verification", False)),
                "recommendation": str(raw.get("recommendation") or "").strip() or None,
            }
        )
    return AgentReview.model_validate(
        {
            "agent_id": slot.id,
            "kind": slot.kind,
            "title": slot.title,
            "provider_profile": provider_profile,
            "model": model,
            "review_source": "service",
            "summary": str(payload.get("summary") or "").strip(),
            "strengths": [str(item).strip() for item in payload.get("strengths", []) if str(item).strip()],
            "weaknesses": [str(item).strip() for item in payload.get("weaknesses", []) if str(item).strip()],
            "recommendation": _safe_decision(payload.get("recommendation")),
            "findings": findings,
        }
    )


def _save_agent_review(job_id: str, slot: AgentSlotConfig, review: AgentReview) -> AgentSummary:
    stem = _agent_artifact_stem(slot)
    folder = reviews_dir(job_id)
    md_path = folder / f"{stem}.md"
    json_path = folder / f"{stem}.json"
    review.markdown = render_agent_markdown(review)
    write_text_atomic(md_path, review.markdown)
    write_json_atomic(json_path, review.model_dump(mode="json"))
    summary = AgentSummary(
        agent_id=slot.id,
        kind=slot.kind,
        title=slot.title,
        category=slot.category,
        status=review.status,
        artifact_markdown=str(md_path),
        artifact_json=str(json_path),
    )

    def apply(job):
        job.agents = [item for item in job.agents if item.agent_id != slot.id] + [summary]

    mutate_job_state(job_id, apply)
    return summary


def _run_slot(
    *,
    registry: ProviderRegistry,
    slot: AgentSlotConfig,
    title: str,
    source_name: str,
    indexed_text: str,
    page_index: dict[int, list[str]],
    provider_override: str | None,
    journal_requirements: str | None,
    paradigm: ManuscriptParadigm | None = None,
    paradigm_criteria: ParadigmCriteriaConfig | None = None,
) -> tuple[AgentSlotConfig, AgentReview, dict[str, int]]:
    provider_profile = provider_override or slot.profile
    provider = registry.build(provider_profile, model_override=slot.model)
    prompt = _build_review_prompt(slot, title, source_name, indexed_text, journal_requirements, paradigm=paradigm, paradigm_criteria=paradigm_criteria)
    seed_refs = seed_evidence_refs(page_index, limit=6)
    result = provider.run_review(
        prompt=prompt,
        context={"slot": slot, "evidence_refs": seed_refs, "evidence_spans": seed_refs},
    )
    review = normalize_review_payload(
        slot=slot,
        provider_profile=provider_profile,
        model=slot.model or registry.get_profile(provider_profile).model,
        page_index=page_index,
        payload=result.payload,
    )
    return slot, review, result.usage


def _refs_overlap(left: list[EvidenceRef], right: list[EvidenceRef]) -> bool:
    for lhs in left:
        for rhs in right:
            if lhs.page is not None and rhs.page is not None and lhs.page == rhs.page:
                l_start = lhs.start_line or 1
                l_end = lhs.end_line or l_start
                r_start = rhs.start_line or 1
                r_end = rhs.end_line or r_start
                if not (l_end < r_start or r_end < l_start):
                    return True
            if lhs.quote and rhs.quote and slugify(lhs.quote[:120]) == slugify(rhs.quote[:120]):
                return True
    return False


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


def _save_concerns(job_id: str, concerns: list[Concern]) -> None:
    paths = ensure_artifact_paths(job_id)
    write_json_atomic(paths["concerns_json"], [item.model_dump(mode="json") for item in concerns])
    with paths["concerns_csv"].open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "id",
                "issue_key",
                "title",
                "description",
                "category",
                "severity",
                "raised_by",
                "specialist_flags",
                "needs_external_verification",
                "consensus_state",
            ],
        )
        writer.writeheader()
        for concern in concerns:
            writer.writerow(
                {
                    "id": concern.id,
                    "issue_key": concern.issue_key,
                    "title": concern.title,
                    "description": concern.description,
                    "category": concern.category,
                    "severity": concern.severity,
                    "raised_by": ";".join(concern.raised_by),
                    "specialist_flags": ";".join(concern.specialist_flags),
                    "needs_external_verification": concern.needs_external_verification,
                    "consensus_state": concern.consensus_state,
                }
            )

    def apply(job):
        job.concerns_count = len(concerns)

    mutate_job_state(job_id, apply)


def _run_editor(
    *,
    registry: ProviderRegistry,
    slot: AgentSlotConfig,
    title: str,
    concerns: list[Concern],
    reviews: list[AgentReview],
    provider_override: str | None,
    journal_requirements: str | None,
    paradigm: ManuscriptParadigm | None = None,
) -> tuple[EditorReport, dict[str, int]]:
    provider_profile = provider_override or slot.profile
    provider = registry.build(provider_profile, model_override=slot.model)
    result = provider.run_editor(
        prompt=_build_editor_prompt(title, concerns, reviews, journal_requirements, paradigm=paradigm),
        context={"concerns": concerns, "reviews": reviews},
    )
    payload = result.payload
    editor = EditorReport.model_validate(
        {
            "agent_id": slot.id,
            "title": slot.title,
            "provider_profile": provider_profile,
            "model": slot.model or registry.get_profile(provider_profile).model,
            "decision": _safe_decision(payload.get("decision")),
            "consensus": payload.get("consensus", []),
            "disagreements": payload.get("disagreements", []),
            "priority_revisions": payload.get("priority_revisions", []),
            "decision_rationale": payload.get("decision_rationale", ""),
        }
    )
    editor.markdown = render_editor_markdown(editor)
    return editor, result.usage


def build_final_report(
    title: str,
    reviews: list[AgentReview],
    concerns: list[Concern],
    editor: EditorReport,
    *,
    journal_requirements: str | None = None,
    journal_context_source: str | None = None,
    layout_fidelity: str | None = None,
    expected_subagent_reviews: int | None = None,
    paradigm: ManuscriptParadigm | None = None,
    revision_review_result: RevisionResponseReview | None = None,
    revision_context_present: bool = False,
) -> str:
    reviews = [with_inferred_review_source(item) for item in reviews]
    source_audit = summarize_review_sources(reviews, expected_subagent_reviews=expected_subagent_reviews)
    strengths = []
    weaknesses = []
    for review in reviews:
        strengths.extend(review.strengths[:1])
        weaknesses.extend(review.weaknesses[:1])
    decision_counts: dict[str, int] = {}
    for review in reviews:
        decision_counts[review.recommendation] = decision_counts.get(review.recommendation, 0) + 1
    consensus_titles = [f"- {item.title}" for item in concerns if item.consensus_state == "consensus"][:8]
    disagreement_titles = [f"- {item.title}" for item in concerns if item.consensus_state != "consensus"][:8]
    actionable = [
        f"- 围绕“{item.title}”修改对应章节，并重新核对所引用证据。"
        for item in concerns[:8]
    ]
    top_concerns = concerns[:12]

    lines = [
        f"# 审稿总报告：{title}",
        "",
        f"- 最终结论：`{_decision_label(editor.decision)}`",
        f"- 已完成审稿份数：`{sum(item.status == 'completed' for item in reviews)}`",
        f"- 合并后问题数：`{len(concerns)}`",
        "",
        "## 评审来源说明",
    ]
    if expected_subagent_reviews is not None:
        lines.extend(
            [
                f"- 预期真实 subagent reviewer：`{source_audit['expected_subagent_reviews']}`",
                f"- 已完成真实 subagent reviewer：`{source_audit['completed_subagent_reviews']}`",
                f"- 主线程本地补写 reviewer：`{source_audit['completed_local_reviews']}`",
                f"- 后端服务 reviewer：`{source_audit['completed_service_reviews']}`",
                f"- 未标注来源 reviewer：`{source_audit['completed_unknown_source_reviews']}`",
                f"- 缺失 subagent 槽位：`{source_audit['missing_subagent_slots']}`",
            ]
        )
        if source_audit["missing_subagent_slots"] or source_audit["completed_local_reviews"] or source_audit["completed_unknown_source_reviews"]:
            lines.append("- 本次未达到完整的 8 个真实 subagent reviewer 配置，不能将本地补写或未标注来源的意见等同于独立 subagent。")
    else:
        lines.extend(
            [
                f"- 后端服务 reviewer：`{source_audit['completed_service_reviews']}`",
                f"- 真实 subagent reviewer：`{source_audit['completed_subagent_reviews']}`",
                f"- 主线程本地 reviewer：`{source_audit['completed_local_reviews']}`",
                f"- 未标注来源 reviewer：`{source_audit['completed_unknown_source_reviews']}`",
            ]
        )
    lines.extend(["", "## 手稿范式分类"])
    if paradigm is not None and paradigm is not FALLBACK_PARADIGM and paradigm.paradigm_labels:
        lines.append(f"- 粗粒度分类：`{paradigm.coarse_family}`")
        for lb in paradigm.paradigm_labels:
            primary_mark = " (主要)" if lb.primary else ""
            lines.append(f"- 范式标签：`{lb.label}` 置信度 {lb.confidence:.0%}{primary_mark}")
        if paradigm.rationale:
            lines.append(f"- 分类依据：{paradigm.rationale}")
    elif paradigm is FALLBACK_PARADIGM:
        lines.append("- 分类状态：未分类（分类步骤失败）")
        lines.append("- 审稿人已被指示根据论文的明显范式应用适当标准")
    else:
        lines.append("- 本次未进行范式分类")
    lines.extend(
        [
            "",
        "## 执行摘要",
        editor.decision_rationale or "委员会建议根据下列有证据支撑的问题进行修改。",
        "",
        "## 共识问题",
        ]
    )
    lines.extend([f"- {item}" for item in editor.consensus] or ["- 未提炼出稳定共识。"])
    lines.extend(["", "## 主要分歧"])
    lines.extend([f"- {item}" for item in editor.disagreements] or ["- 未提炼出明显分歧。"])
    lines.extend(["", "## 论文优点"])
    lines.extend([f"- {item}" for item in strengths] or ["- 未稳定提炼出优点。"])
    lines.extend(["", "## 主要不足"])
    lines.extend([f"- {item}" for item in weaknesses] or ["- 未稳定提炼出不足。"])
    lines.extend(["", "## 期刊要求说明"])
    if journal_requirements and journal_requirements.strip():
        lines.append(f"- 本次已纳入期刊要求，来源：`{journal_context_source or '未注明'}`。")
        lines.append("- 涉及期刊适配的问题已尽量与论文本身质量问题分开表述。")
    else:
        lines.append("- 本次未提供额外的期刊要求。")
    lines.extend(["", "## 提取说明"])
    if layout_fidelity and layout_fidelity != "full":
        lines.append(
            f"- 本次使用 `{layout_fidelity}` 级别的提取保真度，版式、图表与公式仍建议人工复核。"
        )
    else:
        lines.append("- 本次使用了当前可用的最高提取保真度。")
    lines.extend(["", "## 关键问题"])
    lines.extend(consensus_titles or ["- 未提炼出有证据支撑的共识问题。"])
    if disagreement_titles:
        lines.extend(["", "审稿人存在分歧的问题："])
        lines.extend(disagreement_titles)
    lines.extend(["", "## 可执行修改建议"])
    lines.extend(actionable or ["- 将每一项关键论断与明确证据对齐，并收紧措辞。"])
    lines.extend(["", "## 优先修订计划"])
    lines.extend([f"- {item}" for item in editor.priority_revisions] or ["- 先处理严重程度最高且有证据支撑的问题。"])
    lines.extend(["", "## 期刊适配备注"])
    if journal_requirements and journal_requirements.strip():
        lines.extend(
            [
                "- 建议先处理论文本身的证据、论证与结构问题，再处理仅与目标期刊相关的适配问题。",
                "- 如果后续继续投本刊，请把期刊规范问题与核心学术问题分开逐项完成。",
            ]
        )
    else:
        lines.append("- 本次未单独要求期刊适配分析。")
    lines.extend(["", "## 问题清单"])
    if not top_concerns:
        lines.append("- 未合并出有证据支撑的问题条目。")
    else:
        lines.append("- 下列问题为审稿总报告摘要版，只保留问题概述，不附原文正文或长段证据摘录。")
    for concern in top_concerns:
        lines.append(f"### {concern.title}")
        lines.append(f"- 类别：`{concern.category}`")
        lines.append(f"- 严重程度：`{_severity_label(concern.severity)}`")
        lines.append(f"- 提出来源：{', '.join(concern.raised_by)}")
        lines.append(f"- 共识状态：`{_consensus_state_label(concern.consensus_state)}`")
        if concern.specialist_flags:
            lines.append(f"- 专家标记：{', '.join(concern.specialist_flags)}")
        if concern.needs_external_verification:
            lines.append("- 需要外部核查：是")
        lines.append(f"- 问题说明：{concern.description}")
        if journal_requirements and "journal" in concern.category.lower():
            lines.append("- 该问题主要属于期刊适配层面的关注点。")
        lines.append("")
    if revision_review_result is not None:
        lines.extend(["", revision_review_result.markdown])
    elif revision_context_present:
        lines.extend(["", "## 返修回应审稿", "", "⚠️ 检测到返修上下文但返修回应审稿未完成。", ""])
    lines.extend(["## 评分"])
    lines.append(f"- 最终结论：`{_decision_label(editor.decision)}`")
    for key, value in sorted(decision_counts.items()):
        lines.append(f"- {_decision_label(key)}：{value}")
    return "\n".join(lines).strip() + "\n"


def _build_final_summary(
    *,
    job_id: str,
    title: str,
    source_name: str,
    reviews: list[AgentReview],
    concerns: list[Concern],
    editor: EditorReport,
    state_metadata: dict[str, Any],
) -> dict[str, Any]:
    source_audit = summarize_review_sources(reviews)
    return {
        "job_id": job_id,
        "title": title,
        "source_name": source_name,
        "decision": editor.decision,
        "concerns_count": len(concerns),
        "completed_reviews": sum(item.status == "completed" for item in reviews),
        "failed_reviews": sum(item.status != "completed" for item in reviews),
        "layout_fidelity": state_metadata.get("layout_fidelity"),
        "extractor_used": state_metadata.get("extractor_used"),
        "conversion_used": state_metadata.get("conversion_used"),
        "journal_context_present": state_metadata.get("journal_context_present", False),
        "journal_context_source": state_metadata.get("journal_context_source"),
        "mineru_attempted": state_metadata.get("mineru_attempted"),
        "mineru_succeeded": state_metadata.get("mineru_succeeded"),
        "revision_context_present": state_metadata.get("revision_context_present", False),
        "revision_context_source": state_metadata.get("revision_context_source"),
        "revision_extraction_quality": state_metadata.get("revision_extraction_quality"),
        **source_audit,
        "reviewers": [
            {
                "agent_id": review.agent_id,
                "kind": review.kind,
                "recommendation": review.recommendation,
                "status": review.status,
                "review_source": with_inferred_review_source(review).review_source,
            }
            for review in reviews
        ],
    }


def process_job(job_id: str) -> None:
    settings = get_settings()
    state = load_job_state(job_id)
    if state is None:
        return
    registry = ProviderRegistry()
    plan = load_review_plan()
    paths = ensure_artifact_paths(job_id)
    source_path = Path(state.artifacts.source_pdf_path or source_input_path(job_id))
    journal_requirements = (
        paths["journal_requirements"].read_text(encoding="utf-8")
        if paths["journal_requirements"].exists()
        else None
    )
    try:
        _set_status(job_id, JobStatus.pdf_parsing, "Preparing the paper into shared evidence.")
        evidence = prepare_document_once(job_id, source_path, settings)
        append_event(
            job_id,
            "document_prepared",
            extractor_used=evidence.extractor_used,
            layout_fidelity=evidence.layout_fidelity,
            conversion_used=evidence.conversion_used,
            mineru_attempted=evidence.diagnostics.get("mineru_attempted") if evidence.diagnostics else None,
            mineru_succeeded=evidence.diagnostics.get("mineru_succeeded") if evidence.diagnostics else None,
            warning=evidence.warning,
        )

        def apply_prepared(job):
            job.normalized_source_path = str(evidence.normalized_source_path) if evidence.normalized_source_path else None
            job.layout_fidelity = evidence.layout_fidelity
            job.extractor_used = evidence.extractor_used
            job.conversion_used = evidence.conversion_used
            job.journal_context_present = bool(journal_requirements and journal_requirements.strip())
            job.journal_context_source = str(job.metadata.get("journal_context_source") or "text/file")
            job.mineru_attempted = bool(evidence.diagnostics.get("mineru_attempted")) if evidence.diagnostics else None
            job.mineru_succeeded = bool(evidence.diagnostics.get("mineru_succeeded")) if evidence.diagnostics else None
            job.metadata["page_index_path"] = str(paths["page_index"])
            job.metadata["structured_content_path"] = str(paths["structured_content"])
            job.metadata["diagnostics_path"] = str(paths["diagnostics"])
            job.artifacts.source_pdf_path = str(source_path)
            job.artifacts.mineru_markdown_path = str(paths["normalized_markdown"])
            job.artifacts.mineru_content_list_path = str(paths["structured_content"])
            if journal_requirements and journal_requirements.strip():
                job.metadata["journal_requirements_path"] = str(paths["journal_requirements"])
                job.metadata["journal_context_source"] = str(job.metadata.get("journal_context_source") or "text/file")

        mutate_job_state(job_id, apply_prepared)
        refreshed_state = load_job_state(job_id) or state

        indexed_text = serialize_page_index(evidence.page_index, settings.max_evidence_chars)
        source_name = refreshed_state.source_name or source_path.name

        paradigm_criteria = load_paradigm_criteria()
        classification_provider = registry.build(
            state.provider_override or plan.generalists[0].profile,
            model_override=None,
        )
        paradigm = classify_manuscript(
            provider=classification_provider,
            indexed_text=indexed_text,
            page_index=evidence.page_index,
            criteria=paradigm_criteria,
        )

        def apply_classification(job):
            job.manuscript_paradigm = paradigm

        mutate_job_state(job_id, apply_classification)
        append_event(
            job_id,
            "manuscript_classified",
            coarse_family=str(paradigm.coarse_family),
            paradigm_labels=[lb.label for lb in paradigm.paradigm_labels],
            is_fallback=paradigm is FALLBACK_PARADIGM,
        )

        reviews: list[AgentReview] = []
        _set_status(job_id, JobStatus.agent_running, "Running the three generalist reviewers.")
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(
                    _run_slot,
                    registry=registry,
                    slot=slot,
                    title=state.title,
                    source_name=source_name,
                    indexed_text=indexed_text,
                    page_index=evidence.page_index,
                    provider_override=state.provider_override,
                    journal_requirements=journal_requirements,
                    paradigm=paradigm,
                    paradigm_criteria=paradigm_criteria,
                ): slot
                for slot in plan.generalists
            }
            for future in as_completed(futures):
                slot = futures[future]
                try:
                    slot_cfg, review, usage = future.result()
                    _update_usage(job_id, usage)
                    _save_agent_review(job_id, slot_cfg, review)
                    reviews.append(review)
                except Exception as exc:
                    append_event(job_id, "agent_failed", agent_id=slot.id, error=str(exc))
                    failed = AgentReview(
                        agent_id=slot.id,
                        kind=slot.kind,
                        title=slot.title,
                        provider_profile=state.provider_override or slot.profile,
                        model=slot.model or registry.get_profile(state.provider_override or slot.profile).model,
                        review_source="service",
                        status="failed",
                        error=str(exc),
                    )
                    _save_agent_review(job_id, slot, failed)
                    reviews.append(failed)

        append_event(job_id, "committee_complete", completed=sum(item.status == "completed" for item in reviews if item.kind == "generalist"))
        _set_status(job_id, JobStatus.agent_running, "Running the five specialist reviewers.")
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(
                    _run_slot,
                    registry=registry,
                    slot=slot,
                    title=state.title,
                    source_name=source_name,
                    indexed_text=indexed_text,
                    page_index=evidence.page_index,
                    provider_override=state.provider_override,
                    journal_requirements=journal_requirements,
                    paradigm=paradigm,
                    paradigm_criteria=paradigm_criteria,
                ): slot
                for slot in plan.specialists
            }
            for future in as_completed(futures):
                slot = futures[future]
                try:
                    slot_cfg, review, usage = future.result()
                    _update_usage(job_id, usage)
                    _save_agent_review(job_id, slot_cfg, review)
                    reviews.append(review)
                except Exception as exc:
                    append_event(job_id, "agent_failed", agent_id=slot.id, error=str(exc))
                    failed = AgentReview(
                        agent_id=slot.id,
                        kind=slot.kind,
                        title=slot.title,
                        provider_profile=state.provider_override or slot.profile,
                        model=slot.model or registry.get_profile(state.provider_override or slot.profile).model,
                        review_source="service",
                        status="failed",
                        error=str(exc),
                    )
                    _save_agent_review(job_id, slot, failed)
                    reviews.append(failed)

        completed_reviews = [item for item in reviews if item.status == "completed"]
        concerns = merge_concerns(completed_reviews)
        _save_concerns(job_id, concerns)
        append_event(job_id, "concerns_merged", count=len(concerns))

        _set_status(job_id, JobStatus.final_report_persisting, "Generating the meta review and final report.")
        editor, usage = _run_editor(
            registry=registry,
            slot=plan.editor,
            title=state.title,
            concerns=concerns,
            reviews=completed_reviews,
            provider_override=state.provider_override,
            journal_requirements=journal_requirements,
            paradigm=paradigm,
        )
        _update_usage(job_id, usage)
        write_text_atomic(paths["meta_review_md"], render_editor_markdown(editor))
        write_json_atomic(paths["meta_review_json"], editor.model_dump(mode="json"))

        # --- revision response review ---
        revision_review_result: RevisionResponseReview | None = None
        if refreshed_state.revision_context_present:
            revision_notes_path = paths.get("revision_notes")
            if revision_notes_path and revision_notes_path.exists():
                revision_text = revision_notes_path.read_text(encoding="utf-8")
                prev_path = paths.get("previous_review")
                previous_text = prev_path.read_text(encoding="utf-8") if (prev_path and prev_path.exists()) else None
                try:
                    revision_review_result = review_revision_response(
                        provider=registry.build(plan.editor.profile),
                        revision_text=revision_text,
                        concerns=concerns,
                        previous_review_markdown=previous_text,
                        title=refreshed_state.title,
                    )
                    write_text_atomic(paths["revision_response_review_md"], revision_review_result.markdown)
                    write_json_atomic(paths["revision_response_review_json"], revision_review_result.json_payload)
                    if revision_review_result.usage:
                        _update_usage(job_id, revision_review_result.usage)
                    append_event(job_id, "revision_response_review_completed",
                                 quality=revision_review_result.revision_notes_quality)
                except Exception as exc:
                    append_event(job_id, "revision_response_review_failed", error=str(exc))
                    mutate_job_state(job_id, lambda j: setattr(j, "revision_extraction_quality", f"review_failed: {exc}"))

        final_markdown = build_final_report(
            refreshed_state.title,
            completed_reviews,
            concerns,
            editor,
            journal_requirements=journal_requirements,
            journal_context_source=refreshed_state.journal_context_source or refreshed_state.metadata.get("journal_context_source"),
            layout_fidelity=evidence.layout_fidelity,
            paradigm=paradigm,
            revision_review_result=revision_review_result,
            revision_context_present=refreshed_state.revision_context_present,
        )
        validation = validate_final_report(
            markdown=final_markdown,
            min_english_words=0,
            min_chinese_chars=0,
            force_english_output=settings.force_english_output,
        )
        if not validation.ok:
            raise RuntimeError(f"Final report validation failed: {validation.message}")
        write_text_atomic(paths["final_markdown"], final_markdown)

        final_summary = _build_final_summary(
            job_id=job_id,
            title=refreshed_state.title,
            source_name=source_name,
            reviews=reviews,
            concerns=concerns,
            editor=editor,
            state_metadata={
                "layout_fidelity": evidence.layout_fidelity,
                "extractor_used": evidence.extractor_used,
                "conversion_used": evidence.conversion_used,
                "journal_context_present": bool(journal_requirements and journal_requirements.strip()),
                "journal_context_source": refreshed_state.journal_context_source or refreshed_state.metadata.get("journal_context_source"),
                "mineru_attempted": evidence.diagnostics.get("mineru_attempted") if evidence.diagnostics else None,
                "mineru_succeeded": evidence.diagnostics.get("mineru_succeeded") if evidence.diagnostics else None,
                "revision_context_present": refreshed_state.revision_context_present,
                "revision_context_source": refreshed_state.revision_context_source,
                "revision_extraction_quality": refreshed_state.revision_extraction_quality,
            },
        )
        write_json_atomic(paths["final_summary"], final_summary)

        annotations = concerns_to_annotations(concerns)
        write_json_atomic(
            paths["annotations"],
            {"annotations": [item.model_dump(mode="json") for item in annotations], "count": len(annotations)},
        )
        _set_status(job_id, JobStatus.pdf_exporting, "Rendering the final report PDF.")
        latest_state = load_job_state(job_id)
        pdf_source = paths["normalized_pdf"] if paths["normalized_pdf"].exists() else source_path
        pdf_generated = export_pdf_report(
            settings=settings,
            job_id=job_id,
            title=refreshed_state.title,
            source_name=source_name,
            source_pdf_path=pdf_source,
            final_markdown=final_markdown,
            content_list=evidence.content_list,
            annotations=annotations,
            token_usage=latest_state.usage.token.model_dump() if latest_state else {"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            agent_model=editor.model,
            report_pdf_path=paths["report_pdf"],
        )
        latest_state = load_job_state(job_id)
        if latest_state is not None:
            write_json_atomic(paths["usage_summary"], latest_state.usage.model_dump(mode="json"))
        alias_files = write_friendly_artifact_aliases(
            job_id,
            title=refreshed_state.title,
            source_name=source_name,
        )

        def apply(job):
            job.status = JobStatus.completed
            job.message = "Review completed."
            job.annotation_count = len(annotations)
            job.final_report_ready = True
            job.pdf_ready = pdf_generated and paths["report_pdf"].exists()
            job.concerns_count = len(concerns)
            job.decision = editor.decision
            job.artifacts.source_pdf_path = str(source_path)
            job.artifacts.mineru_markdown_path = str(paths["normalized_markdown"])
            job.artifacts.mineru_content_list_path = str(paths["structured_content"])
            job.artifacts.annotations_path = str(paths["annotations"])
            job.artifacts.final_markdown_path = str(paths["final_markdown"])
            job.artifacts.report_pdf_path = str(paths["report_pdf"]) if paths["report_pdf"].exists() else None
            job.metadata["page_index_path"] = str(paths["page_index"])
            job.metadata["meta_review_path"] = str(paths["meta_review_md"])
            job.metadata["final_summary_path"] = str(paths["final_summary"])
            if alias_files:
                job.metadata["friendly_artifact_aliases"] = alias_files
                if alias_files.get("latest_results_dir"):
                    job.metadata["latest_results_dir"] = alias_files["latest_results_dir"]
                if alias_files.get("paper_results_dir"):
                    job.metadata["paper_results_dir"] = alias_files["paper_results_dir"]

        mutate_job_state(job_id, apply)
        append_event(job_id, "completed", concerns=len(concerns), decision=editor.decision, pdf_ready=pdf_generated)
    except Exception as exc:
        _fail_job(
            job_id,
            "Fusion review failed.",
            "".join(traceback.format_exception_only(type(exc), exc)).strip(),
        )
