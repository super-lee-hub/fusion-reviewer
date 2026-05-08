import json
from pathlib import Path

import pytest

import fusion_reviewer.config as config_module
from fusion_reviewer.codex_repair import (
    finalize_codex_run_from_payload_files,
    rebuild_codex_run_from_reviews,
    repair_codex_run,
)
from fusion_reviewer.models import AgentReview


def _write_review(path: Path, *, agent_id: str, title: str, issue_key: str, category: str) -> None:
    payload = AgentReview.model_validate(
        {
            "agent_id": agent_id,
            "kind": "generalist",
            "title": title,
            "provider_profile": "codex_skill",
            "model": "gpt-5.4-mini",
            "status": "completed",
            "summary": "summary",
            "strengths": ["strength"],
            "weaknesses": ["weakness"],
            "recommendation": "major_revision",
            "findings": [
                {
                    "id": f"{agent_id}-{issue_key}",
                    "issue_key": issue_key,
                    "title": title,
                    "description": title,
                    "category": category,
                    "severity": "high",
                    "evidence_refs": [
                        {
                            "locator": "docx para 1",
                            "quote": "这是第一段中文内容，用于测试修复命令。",
                        }
                    ],
                    "recommendation": "fix it",
                }
            ],
        }
    )
    path.write_text(payload.model_dump_json(indent=2), encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolated_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "review_outputs"))
    config_module.get_settings.cache_clear()
    yield
    config_module.get_settings.cache_clear()


def test_repair_codex_run_rebuilds_inputs_and_outputs(tmp_path):
    run_dir = tmp_path / "20260327-170000__测试论文__abc123"
    evidence_dir = run_dir / "evidence"
    reviews_dir = run_dir / "reviews"
    evidence_dir.mkdir(parents=True)
    reviews_dir.mkdir(parents=True)

    (evidence_dir / "source_copy.docx").write_bytes(b"PK")
    (evidence_dir / "normalized.md").write_text("# garbled\n\n浣犲ソ ???", encoding="utf-8")
    (evidence_dir / "plain_text.txt").write_text("浣犲ソ ???", encoding="utf-8")
    (evidence_dir / "page_index.json").write_text('{"1":["浣犲ソ ???"]}', encoding="utf-8")
    (evidence_dir / "diagnostics.json").write_text(
        '{"extractor_used":"fitz","layout_fidelity":"full","conversion_used":"libreoffice->pdf"}',
        encoding="utf-8",
    )
    (evidence_dir / "journal_requirements.md").write_text("# 期刊要求\n\n摘要 100-300 字。", encoding="utf-8")
    (evidence_dir / "prepare_manifest.json").write_text(
        '{"paper_stem":"测试论文","run_dir":"placeholder","source_name":"测试论文.docx"}',
        encoding="utf-8",
    )
    (run_dir / "final_summary.json").write_text(
        '{"title":"测试论文","source_name":"测试论文.docx"}',
        encoding="utf-8",
    )
    (run_dir / "editor_input.json").write_text('{"bad":"???"}', encoding="utf-8")
    (run_dir / "reviews_input.json").write_text('[{"bad":"???"}]', encoding="utf-8")

    _write_review(
        reviews_dir / "committee_review_committee_reviewer_a.json",
        agent_id="committee_reviewer_a",
        title="样本过窄",
        issue_key="sample_scope_overgeneralization",
        category="sample",
    )
    _write_review(
        reviews_dir / "committee_review_committee_reviewer_b.json",
        agent_id="committee_reviewer_b",
        title="框架未操作化",
        issue_key="framework_not_operationalized",
        category="operationalization",
    )

    import fusion_reviewer.codex_repair as repair_module

    original_extract = repair_module.extract_docx_text
    try:
        repair_module.extract_docx_text = lambda path: "\n".join(
            [
                "这是第一段中文内容，用于测试修复命令。",
                "这是第二段中文内容，用于确保 paragraph_index 能被重建。",
                "这是第三段中文内容，用于让 DOCX 原生文本成为新的权威 evidence。",
            ]
        )
        result = repair_codex_run(run_dir, force_docx_evidence=True)
    finally:
        repair_module.extract_docx_text = original_extract

    assert (run_dir / "reviews_input.json").exists()
    assert (run_dir / "editor_input.json").exists()
    assert (run_dir / "final_report.md").exists()
    assert (run_dir / "meta_review.md").exists()
    assert (run_dir / "repair_summary.json").exists()
    assert (run_dir / "_repair_backups").exists()
    assert (evidence_dir / "paragraph_index.json").exists()
    assert result["repair_summary"]["evidence_repair"]["applied"] is True
    assert result["final_summary"]["decision"] in {"major_revision", "minor_revision", "reject", "accept"}


def test_rebuild_codex_run_from_reviews_generates_outputs_without_combined_inputs(tmp_path):
    run_dir = tmp_path / "20260328-010000__测试论文__fromreviews"
    evidence_dir = run_dir / "evidence"
    reviews_dir = run_dir / "reviews"
    evidence_dir.mkdir(parents=True)
    reviews_dir.mkdir(parents=True)

    (evidence_dir / "diagnostics.json").write_text(
        '{"extractor_used":"mineru","layout_fidelity":"full","conversion_used":"libreoffice->pdf"}',
        encoding="utf-8",
    )
    (evidence_dir / "journal_requirements.md").write_text("# 期刊要求\n\n摘要 100-300 字。", encoding="utf-8")
    (evidence_dir / "prepare_manifest.json").write_text(
        '{"paper_stem":"测试论文","run_dir":"placeholder","source_name":"测试论文.docx"}',
        encoding="utf-8",
    )
    (run_dir / "final_summary.json").write_text(
        '{"title":"测试论文","source_name":"测试论文.docx"}',
        encoding="utf-8",
    )

    _write_review(
        reviews_dir / "committee_review_committee_reviewer_a.json",
        agent_id="committee_reviewer_a",
        title="样本过窄",
        issue_key="sample_scope_overgeneralization",
        category="sample",
    )
    _write_review(
        reviews_dir / "specialist_review_structure.json",
        agent_id="specialist_structure",
        title="参考文献格式不统一",
        issue_key="reference_formatting_noncompliance",
        category="structure",
    )

    result = rebuild_codex_run_from_reviews(run_dir, reviews_dir=reviews_dir)

    assert (run_dir / "reviews_input.json").exists()
    assert (run_dir / "editor_input.json").exists()
    assert (run_dir / "final_report.md").exists()
    assert (run_dir / "meta_review.md").exists()
    assert (run_dir / "rebuild_summary.json").exists()
    assert result["rebuild_summary"]["editor_source"] == "generated_from_reviews"
    assert result["rebuild_summary"]["review_count"] == 2


def test_rebuild_codex_run_from_reviews_dedupes_same_agent_with_stale_file(tmp_path):
    run_dir = tmp_path / "20260328-010000__测试论文__duplicates"
    evidence_dir = run_dir / "evidence"
    reviews_dir = run_dir / "reviews"
    evidence_dir.mkdir(parents=True)
    reviews_dir.mkdir(parents=True)

    (evidence_dir / "diagnostics.json").write_text(
        '{"extractor_used":"mineru","layout_fidelity":"full","conversion_used":"libreoffice->pdf"}',
        encoding="utf-8",
    )
    (evidence_dir / "prepare_manifest.json").write_text(
        '{"paper_stem":"测试论文","run_dir":"placeholder","source_name":"测试论文.docx"}',
        encoding="utf-8",
    )

    _write_review(
        reviews_dir / "specialist_review_originality.json",
        agent_id="specialist_significance",
        title="旧文件名",
        issue_key="originality_gap",
        category="originality",
    )
    _write_review(
        reviews_dir / "specialist_review_significance.json",
        agent_id="specialist_significance",
        title="规范文件名",
        issue_key="external_validity_gap",
        category="originality",
    )

    result = rebuild_codex_run_from_reviews(run_dir, reviews_dir=reviews_dir)

    assert result["rebuild_summary"]["review_count"] == 1


def test_finalize_codex_run_from_payload_files_writes_inputs_and_outputs(tmp_path):
    run_dir = tmp_path / "20260328-020000__测试论文__payloads"
    evidence_dir = run_dir / "evidence"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "diagnostics.json").write_text(
        '{"extractor_used":"mineru","layout_fidelity":"full","conversion_used":"libreoffice->pdf"}',
        encoding="utf-8",
    )
    (evidence_dir / "prepare_manifest.json").write_text(
        '{"paper_stem":"测试论文","run_dir":"placeholder","source_name":"测试论文.docx"}',
        encoding="utf-8",
    )

    review_payload = AgentReview.model_validate(
        {
            "agent_id": "committee_reviewer_a",
            "kind": "generalist",
            "title": "委员会审稿人 A",
            "provider_profile": "codex_subagent",
            "model": "gpt-5.4",
            "status": "completed",
            "summary": "summary",
            "strengths": ["strength"],
            "weaknesses": ["weakness"],
            "recommendation": "major_revision",
            "findings": [
                {
                    "id": "a-1",
                    "issue_key": "sample_scope_overgeneralization",
                    "title": "样本过窄",
                    "description": "样本过窄",
                    "category": "sample",
                    "severity": "high",
                    "evidence_refs": [{"locator": "docx para 1", "quote": "这是第一段中文内容，用于测试。"}],
                    "recommendation": "fix it",
                }
            ],
        }
    )
    reviews_file = tmp_path / "tmp_reviews.json"
    reviews_file.write_text(
        json.dumps([review_payload.model_dump(mode="json")], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    editor_file = tmp_path / "tmp_editor.json"
    editor_file.write_text(
        json.dumps(
            {
                "agent_id": "meta_editor",
                "title": "综合审稿编辑",
                "provider_profile": "codex_root",
                "model": "gpt-5.4",
                "decision": "major_revision",
                "consensus": ["需要大修。"],
                "priority_revisions": ["补强证据链。"],
                "decision_rationale": "当前版本仍需大修。",
                "status": "completed",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    result = finalize_codex_run_from_payload_files(
        run_dir,
        reviews_file=reviews_file,
        editor_file=editor_file,
    )

    assert (run_dir / "reviews_input.json").exists()
    assert (run_dir / "editor_input.json").exists()
    assert (run_dir / "final_report.md").exists()
    assert (run_dir / "meta_review.md").exists()
    assert (run_dir / "payload_finalize_summary.json").exists()
    assert result["payload_finalize_summary"]["review_count"] == 1
