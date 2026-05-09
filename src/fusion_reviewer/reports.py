"""Deterministic report building and rendering — no LLM/API calls."""

from __future__ import annotations

from typing import Any

from .evidence import evidence_ref_to_text
from .models import (
    FALLBACK_PARADIGM,
    AgentReview,
    Concern,
    EditorReport,
    EvidenceRef,
    ManuscriptParadigm,
)
from .provenance import summarize_review_sources, with_inferred_review_source

SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4}


# ---- label helpers -----------------------------------------------------------

def decision_label(value: str) -> str:
    return {
        "accept": "接收",
        "minor_revision": "小修",
        "major_revision": "大修",
        "reject": "拒稿",
    }.get(value, value)


def severity_label(value: str) -> str:
    return {
        "low": "低",
        "medium": "中",
        "high": "高",
        "critical": "严重",
    }.get(value, value)


def consensus_state_label(value: str) -> str:
    return {
        "consensus": "多数共识",
        "disagreement": "存在分歧",
        "single-source": "单一来源",
    }.get(value, value)


def kind_label(value: str) -> str:
    return {
        "generalist": "综合审稿人",
        "specialist": "专项审稿人",
        "editor": "编辑",
    }.get(value, value)


def review_source_label(value: str) -> str:
    return {
        "subagent": "真实子代理",
        "local": "主线程本地",
        "service": "后端服务",
        "unknown": "未标注来源",
    }.get(value, value)


# ---- renderers ---------------------------------------------------------------

def render_agent_markdown(review: AgentReview) -> str:
    review = with_inferred_review_source(review)
    lines = [
        f"# {review.title}",
        "",
        f"- 审稿人 ID：`{review.agent_id}`",
        f"- 类型：`{kind_label(review.kind)}`",
        f"- 来源：`{review_source_label(review.review_source)}`",
        f"- Provider 配置：`{review.provider_profile}`",
        f"- 模型：`{review.model}`",
        f"- 审稿建议：`{decision_label(review.recommendation)}`",
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
        lines.append(f"- 严重程度：`{severity_label(finding.severity)}`")
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
        f"- 最终结论：`{decision_label(editor.decision)}`",
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


# ---- report builders ---------------------------------------------------------

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
    revision_review_result: Any | None = None,
    revision_context_present: bool = False,
) -> str:
    reviews = [with_inferred_review_source(item) for item in reviews]
    source_audit = summarize_review_sources(reviews, expected_subagent_reviews=expected_subagent_reviews)
    strengths: list[str] = []
    weaknesses: list[str] = []
    for review in reviews:
        strengths.extend(review.strengths[:1])
        weaknesses.extend(review.weaknesses[:1])
    decision_counts: dict[str, int] = {}
    for review in reviews:
        decision_counts[review.recommendation] = decision_counts.get(review.recommendation, 0) + 1
    consensus_titles = [f"- {item.title}" for item in concerns if item.consensus_state == "consensus"][:8]
    disagreement_titles = [f"- {item.title}" for item in concerns if item.consensus_state != "consensus"][:8]
    actionable = [
        f'- \u56f4\u7ed5\u201c{item.title}\u201d\u4fee\u6539\u5bf9\u5e94\u7ae0\u8282\uff0c\u5e76\u91cd\u65b0\u6838\u5bf9\u6240\u5f15\u7528\u8bc1\u636e\u3002'
        for item in concerns[:8]
    ]
    top_concerns = concerns[:12]

    lines = [
        f"# 审稿总报告：{title}",
        "",
        f"- 最终结论：`{decision_label(editor.decision)}`",
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
        lines.append(f"- 严重程度：`{severity_label(concern.severity)}`")
        lines.append(f"- 提出来源：{', '.join(concern.raised_by)}")
        lines.append(f"- 共识状态：`{consensus_state_label(concern.consensus_state)}`")
        if concern.specialist_flags:
            lines.append(f"- 专家标记：{', '.join(concern.specialist_flags)}")
        if concern.needs_external_verification:
            lines.append("- 需要外部核查：是")
        lines.append(f"- 问题说明：{concern.description}")
        if journal_requirements and "journal" in concern.category.lower():
            lines.append("- 该问题主要属于期刊适配层面的关注点。")
        lines.append("")
    if revision_review_result is not None:
        lines.extend(["", str(getattr(revision_review_result, 'markdown', ''))])
    elif revision_context_present:
        lines.extend(["", "## 返修回应审稿", "", "⚠️ 检测到返修上下文但返修回应审稿未完成。", ""])
    lines.extend(["## 评分"])
    lines.append(f"- 最终结论：`{decision_label(editor.decision)}`")
    for key, value in sorted(decision_counts.items()):
        lines.append(f"- {decision_label(key)}：{value}")
    return "\n".join(lines).strip() + "\n"


def build_final_summary(
    *,
    run_id: str,
    title: str,
    source_name: str,
    reviews: list[AgentReview],
    concerns: list[Concern],
    editor: EditorReport,
    state_metadata: dict[str, Any],
) -> dict[str, Any]:
    source_audit = summarize_review_sources(reviews)
    return {
        "run_id": run_id,
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
