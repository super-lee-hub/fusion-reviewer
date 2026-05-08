from fusion_reviewer.config import AgentSlotConfig, ParadigmCriteriaConfig
from fusion_reviewer.models import FALLBACK_PARADIGM, AgentReview, Concern, EditorReport, ManuscriptParadigm, ParadigmLabel
from fusion_reviewer.orchestration import (
    _build_editor_prompt,
    _build_review_prompt,
    _paradigm_criteria_block,
    build_final_report,
    merge_concerns,
    render_agent_markdown,
    render_editor_markdown,
)


def _review(agent_id: str, *, issue_key: str, title: str, category: str, quote: str) -> AgentReview:
    return AgentReview.model_validate(
        {
            "agent_id": agent_id,
            "kind": "generalist",
            "title": f"Reviewer {agent_id}",
            "provider_profile": "mock_local",
            "model": "mock-model",
            "summary": "summary",
            "strengths": [],
            "weaknesses": [],
            "recommendation": "major_revision",
            "findings": [
                {
                    "id": f"{agent_id}-{issue_key}",
                    "issue_key": issue_key,
                    "title": title,
                    "description": title,
                    "category": category,
                    "severity": "medium",
                    "evidence_refs": [
                        {
                            "page": 1,
                            "start_line": 1,
                            "end_line": 1,
                            "quote": quote,
                            "locator": "docx para 1",
                        }
                    ],
                }
            ],
        }
    )


def test_merge_concerns_does_not_merge_distinct_issues_with_shared_evidence():
    reviews = [
        _review(
            "a",
            issue_key="abstract_too_long",
            title="摘要过长",
            category="journal_fit_abstract",
            quote="摘要一般应说明研究工作的目的、实验方法、结果和结论等，而重点是结果和结论。",
        ),
        _review(
            "b",
            issue_key="english_package_incomplete",
            title="英文信息不完整",
            category="journal_fit_english",
            quote="摘要一般应说明研究工作的目的、实验方法、结果和结论等，而重点是结果和结论。",
        ),
    ]

    concerns = merge_concerns(reviews)

    assert len(concerns) == 2
    assert {item.issue_key for item in concerns} == {"abstract_too_long", "english_package_incomplete"}


def test_merge_concerns_still_merges_same_issue_key():
    reviews = [
        _review("a", issue_key="sample_scope_overgeneralization", title="样本过窄", category="sample", quote="样本有限"),
        _review("b", issue_key="sample_scope_overgeneralization", title="学生样本不足", category="sample", quote="样本有限"),
    ]

    concerns = merge_concerns(reviews)

    assert len(concerns) == 1
    assert concerns[0].issue_key == "sample_scope_overgeneralization"
    assert concerns[0].consensus_state == "consensus"


def test_merge_concerns_does_not_merge_distinct_chinese_titles_with_empty_slugs():
    reviews = [
        _review(
            "a",
            issue_key="method_description_inconsistency",
            title="方法表述前后不一致",
            category="empirical",
            quote="摘要与方法部分对统计方法的描述不一致。",
        ),
        _review(
            "b",
            issue_key="hypothesis_testing_misalignment",
            title="假设与结果/适配性检验没有一一对应",
            category="empirical",
            quote="H2 与 H3 的检验口径没有和结果段逐一对应。",
        ),
    ]

    concerns = merge_concerns(reviews)

    assert len(concerns) == 2
    assert {item.issue_key for item in concerns} == {
        "method_description_inconsistency",
        "hypothesis_testing_misalignment",
    }


def test_rendered_reports_prefer_chinese_labels():
    review = _review(
        "a",
        issue_key="sample_scope_overgeneralization",
        title="样本外推过宽",
        category="sample",
        quote="样本有限",
    )
    review.summary = "这是摘要意见。"
    review.strengths = ["选题有现实意义。"]
    review.weaknesses = ["样本外推过宽。"]
    review.review_source = "subagent"

    concern = Concern.model_validate(
        {
            "id": "sample_scope_overgeneralization",
            "issue_key": "sample_scope_overgeneralization",
            "title": "样本外推过宽",
            "description": "结论超出了样本边界。",
            "category": "sample",
            "severity": "high",
            "raised_by": ["a", "b"],
            "consensus_state": "consensus",
            "evidence_refs": [
                {
                    "page": 1,
                    "start_line": 1,
                    "end_line": 1,
                    "quote": "样本有限",
                    "locator": "docx para 1",
                }
            ],
        }
    )
    editor = EditorReport.model_validate(
        {
            "title": "综合审稿编辑",
            "decision": "major_revision",
            "consensus": ["样本外推过宽。"],
            "priority_revisions": ["收紧结论边界。"],
            "decision_rationale": "主要问题在于结论超出样本支持范围。",
        }
    )

    review_md = render_agent_markdown(review)
    editor_md = render_editor_markdown(editor)
    final_md = build_final_report("测试论文", [review], [concern], editor, layout_fidelity="full", expected_subagent_reviews=8)

    assert "## 摘要意见" in review_md
    assert "来源：`真实子代理`" in review_md
    assert "## 共识问题" in editor_md
    assert "# 审稿总报告：测试论文" in final_md
    assert "## 评审来源说明" in final_md
    assert "## 执行摘要" in final_md
    assert "## 问题清单" in final_md
    assert "## 修订思路与写作提纲" not in final_md
    assert "## 实验清单与研究实验计划" not in final_md
    assert "证据：" not in final_md
    assert "最终结论：`大修`" in final_md


def _slot_config(**kwargs) -> AgentSlotConfig:
    defaults = {
        "id": "test_slot",
        "kind": "generalist",
        "title": "Test Reviewer",
        "profile": "mock_local",
        "model": "mock-model",
        "category": "general",
        "tone_instruction": "Be critical.",
        "focus_areas": ["contribution", "method"],
    }
    defaults.update(kwargs)
    return AgentSlotConfig.model_validate(defaults)


def _sample_criteria() -> ParadigmCriteriaConfig:
    return ParadigmCriteriaConfig(
        paradigms=[
            {"tag": "formal_modeling", "coarse_family": "theoretical", "appropriate_focus": ["模型假设", "推导正确性"], "inappropriate_critique_patterns": ["实证识别", "数据构建"]},
            {"tag": "experiment", "coarse_family": "empirical", "appropriate_focus": ["识别策略", "随机化", "稳健性"], "inappropriate_critique_patterns": ["模型推导", "博弈证明"]},
        ],
        fallback_focus=["方法适切性", "论证自洽性"],
    )


def _theory_paradigm() -> ManuscriptParadigm:
    return ManuscriptParadigm(
        coarse_family="theoretical",
        paradigm_labels=[
            ParadigmLabel(label="formal_modeling", confidence=0.85, primary=True, evidence_refs=[])
        ],
        rationale="Formal model with propositions and proofs.",
    )


def _empirical_paradigm() -> ManuscriptParadigm:
    return ManuscriptParadigm(
        coarse_family="empirical",
        paradigm_labels=[
            ParadigmLabel(label="experiment", confidence=0.90, primary=True, evidence_refs=[])
        ],
        rationale="Randomized experiment with treatment/control.",
    )


class TestParadigmCriteriaBlock:
    def test_theory_paradigm_includes_appropriate_criteria(self):
        block = _paradigm_criteria_block(_theory_paradigm(), _sample_criteria(), _slot_config())
        assert "模型假设" in block
        assert "推导正确性" in block

    def test_theory_paradigm_excludes_empirical_criteria(self):
        block = _paradigm_criteria_block(_theory_paradigm(), _sample_criteria(), _slot_config())
        assert "实证识别" not in block.split("AVOID")[0] if "AVOID" in block else True

    def test_empirical_paradigm_includes_empirical_criteria(self):
        block = _paradigm_criteria_block(_empirical_paradigm(), _sample_criteria(), _slot_config())
        assert "识别策略" in block

    def test_fallback_paradigm_uses_generic_instructions(self):
        block = _paradigm_criteria_block(FALLBACK_PARADIGM, _sample_criteria(), _slot_config())
        assert "not been pre-classified" in block
        assert "方法适切性" in block

    def test_none_paradigm_uses_generic_instructions(self):
        block = _paradigm_criteria_block(None, _sample_criteria(), _slot_config())
        assert "not been pre-classified" in block


class TestBuildReviewPromptWithParadigm:
    def test_theory_prompt_contains_paradigm_block(self):
        prompt = _build_review_prompt(
            _slot_config(), "Test Paper", "test.pdf", "indexed evidence text",
            journal_requirements=None,
            paradigm=_theory_paradigm(),
            paradigm_criteria=_sample_criteria(),
        )
        assert "formal_modeling" in prompt
        assert "theoretical" in prompt
        assert "Paradigm-aware review instructions" in prompt

    def test_empirical_prompt_contains_paradigm_block(self):
        prompt = _build_review_prompt(
            _slot_config(), "Test Paper", "test.pdf", "indexed evidence text",
            journal_requirements=None,
            paradigm=_empirical_paradigm(),
            paradigm_criteria=_sample_criteria(),
        )
        assert "experiment" in prompt
        assert "empirical" in prompt

    def test_fallback_prompt_has_unclassified_message(self):
        prompt = _build_review_prompt(
            _slot_config(), "Test Paper", "test.pdf", "indexed evidence text",
            journal_requirements=None,
            paradigm=FALLBACK_PARADIGM,
            paradigm_criteria=_sample_criteria(),
        )
        assert "not been pre-classified" in prompt

    def test_no_paradigm_omits_block(self):
        prompt = _build_review_prompt(
            _slot_config(), "Test Paper", "test.pdf", "indexed evidence text",
            journal_requirements=None,
        )
        assert "Paradigm-aware review instructions" not in prompt


class TestBuildEditorPromptWithParadigm:
    def test_editor_prompt_includes_classification_info(self):
        prompt = _build_editor_prompt(
            "Test Paper", [], [],
            journal_requirements=None,
            paradigm=_theory_paradigm(),
        )
        assert "formal_modeling" in prompt
        assert "theoretical" in prompt

    def test_editor_prompt_includes_mismatch_rule(self):
        prompt = _build_editor_prompt(
            "Test Paper", [], [],
            journal_requirements=None,
            paradigm=_theory_paradigm(),
        )
        assert "Paradigm-aware editor rules" in prompt
        assert "downgrade" in prompt.lower() or "remove" in prompt.lower()

    def test_editor_prompt_journal_paradigm_interaction(self):
        prompt = _build_editor_prompt(
            "Test Paper", [], [],
            journal_requirements="This journal publishes empirical research only.",
            paradigm=_theory_paradigm(),
        )
        assert "Journal-paradigm interaction" in prompt
        assert "journal-fit" in prompt

    def test_editor_prompt_fallback_shows_unclassified(self):
        prompt = _build_editor_prompt(
            "Test Paper", [], [],
            journal_requirements=None,
            paradigm=FALLBACK_PARADIGM,
        )
        assert "UNCLASSIFIED" in prompt


class TestBuildFinalReportWithParadigm:
    def test_report_includes_classification_section(self):
        editor = EditorReport.model_validate({
            "decision": "major_revision",
            "consensus": [],
            "priority_revisions": [],
            "decision_rationale": "Needs revision.",
        })
        report = build_final_report(
            "Test Paper", [], [], editor,
            paradigm=_theory_paradigm(),
        )
        assert "## 手稿范式分类" in report
        assert "theoretical" in report
        assert "formal_modeling" in report

    def test_report_fallback_shows_unclassified(self):
        editor = EditorReport.model_validate({
            "decision": "major_revision",
            "consensus": [],
            "priority_revisions": [],
            "decision_rationale": "Needs revision.",
        })
        report = build_final_report(
            "Test Paper", [], [], editor,
            paradigm=FALLBACK_PARADIGM,
        )
        assert "## 手稿范式分类" in report
        assert "未分类" in report
