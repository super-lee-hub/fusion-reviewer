from __future__ import annotations

import json
from pathlib import Path

import pytest

import fusion_reviewer.config as config_module
from fusion_reviewer.codex_runtime import finalize_codex_run


def _review(agent_id: str, kind: str, provider_profile: str, category: str) -> dict:
    return {
        "agent_id": agent_id,
        "kind": kind,
        "title": f"Reviewer {agent_id}",
        "provider_profile": provider_profile,
        "model": "mock-model",
        "summary": "summary",
        "strengths": ["strength"],
        "weaknesses": ["weakness"],
        "recommendation": "major_revision",
        "findings": [
            {
                "id": f"{agent_id}-{category}",
                "issue_key": f"{agent_id}_{category}",
                "title": f"{agent_id} {category}",
                "description": "desc",
                "category": category,
                "severity": "medium",
                "evidence_refs": [
                    {
                        "page": 1,
                        "start_line": 1,
                        "end_line": 1,
                        "quote": "quoted evidence",
                        "locator": "docx para 1",
                    }
                ],
            }
        ],
    }


@pytest.fixture(autouse=True)
def _isolated_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "review_outputs"))
    config_module.get_settings.cache_clear()
    yield
    config_module.get_settings.cache_clear()


def test_finalize_codex_run_marks_local_reviews_and_missing_subagent_slots(tmp_path):
    run_dir = tmp_path / "20260328-test-run"
    (run_dir / "evidence").mkdir(parents=True)

    reviews = [
        _review("committee_reviewer_a", "generalist", "codex_subagent", "general"),
        _review("committee_reviewer_b", "generalist", "codex_subagent", "general"),
        _review("committee_reviewer_c", "generalist", "codex_subagent", "general"),
        _review("specialist_theoretical", "specialist", "codex_subagent", "theoretical"),
        _review("specialist_empirical", "specialist", "codex_subagent", "empirical"),
        _review("specialist_clarity", "specialist", "codex_subagent", "clarity"),
        _review("specialist_significance", "specialist", "codex_root", "significance"),
        _review("specialist_structure", "specialist", "codex_root", "structure"),
    ]
    editor = {
        "decision": "major_revision",
        "consensus": ["需要大修。"],
        "priority_revisions": ["补强识别设计。"],
        "decision_rationale": "当前版本尚未形成完整的识别链条。",
        "provider_profile": "codex_root",
        "model": "gpt-5.4",
    }

    payload = finalize_codex_run(
        run_dir,
        title="测试论文",
        source_name="paper.docx",
        reviews=reviews,
        editor=editor,
    )

    final_summary = payload["final_summary"]
    assert final_summary["expected_subagent_reviews"] == 8
    assert final_summary["completed_subagent_reviews"] == 6
    assert final_summary["completed_local_reviews"] == 2
    assert final_summary["missing_subagent_slots"] == 2
    assert final_summary["full_subagent_committee"] is False

    meta_review = json.loads((run_dir / "meta_review.json").read_text(encoding="utf-8"))
    assert meta_review["completed_subagent_reviews"] == 6
    assert meta_review["completed_local_reviews"] == 2
    assert meta_review["missing_subagent_slots"] == 2

    report_text = (run_dir / "final_report.md").read_text(encoding="utf-8")
    assert "缺失 subagent 槽位：`2`" in report_text
    assert "不能将本地补写或未标注来源的意见等同于独立 subagent" in report_text
    assert "测试论文" in payload["latest_results_dir"]

    local_review_path = run_dir / "reviews" / "specialist_review_significance.json"
    local_review = json.loads(local_review_path.read_text(encoding="utf-8"))
    assert local_review["review_source"] == "local"


def test_finalize_codex_run_rejects_shell_corrupted_payloads(tmp_path):
    run_dir = tmp_path / "20260328-shell-corrupted"
    (run_dir / "evidence").mkdir(parents=True)

    corrupted_reviews = [
        {
            "agent_id": "committee_reviewer_a",
            "kind": "generalist",
            "title": "委员会审稿人 A",
            "provider_profile": "codex_subagent",
            "model": "gpt-5.4-mini",
            "summary": "?" * 80,
            "strengths": ["?" * 40],
            "weaknesses": ["?" * 40],
            "recommendation": "major_revision",
            "findings": [
                {
                    "id": "a-1",
                    "issue_key": "corrupted",
                    "title": "?" * 40,
                    "description": "?" * 80,
                    "category": "general",
                    "severity": "medium",
                    "evidence_refs": [{"quote": "?" * 40, "locator": "manual"}],
                }
            ],
        }
    ]
    editor = {
        "decision": "major_revision",
        "consensus": ["?" * 40],
        "priority_revisions": ["?" * 40],
        "decision_rationale": "?" * 80,
        "provider_profile": "codex_root",
        "model": "gpt-5.4",
    }

    with pytest.raises(ValueError, match="shell-corrupted finalize payload"):
        finalize_codex_run(
            run_dir,
            title="测试论文",
            source_name="paper.docx",
            reviews=corrupted_reviews,
            editor=editor,
        )


def test_finalize_codex_run_removes_stale_specialist_review_files(tmp_path):
    run_dir = tmp_path / "20260328-stale-review-files"
    reviews_dir = run_dir / "reviews"
    (run_dir / "evidence").mkdir(parents=True)
    reviews_dir.mkdir(parents=True)

    stale_payload = _review("specialist_significance", "specialist", "codex_subagent", "originality")
    (reviews_dir / "specialist_review_originality.json").write_text(
        json.dumps(stale_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (reviews_dir / "specialist_review_originality.md").write_text("stale", encoding="utf-8")

    reviews = [
        _review("committee_reviewer_a", "generalist", "codex_subagent", "general"),
        _review("committee_reviewer_b", "generalist", "codex_subagent", "general"),
        _review("committee_reviewer_c", "generalist", "codex_subagent", "general"),
        _review("specialist_theoretical", "specialist", "codex_subagent", "theoretical_framework"),
        _review("specialist_empirical", "specialist", "codex_subagent", "research_design"),
        _review("specialist_clarity", "specialist", "codex_subagent", "clarity"),
        stale_payload,
        _review("specialist_structure", "specialist", "codex_subagent", "submission_norms"),
    ]
    editor = {
        "decision": "major_revision",
        "consensus": ["需要大修。"],
        "priority_revisions": ["补强识别设计。"],
        "decision_rationale": "当前版本尚未形成完整的识别链条。",
        "provider_profile": "codex_root",
        "model": "gpt-5.4",
    }

    finalize_codex_run(
        run_dir,
        title="测试论文",
        source_name="paper.docx",
        reviews=reviews,
        editor=editor,
    )

    assert not (reviews_dir / "specialist_review_originality.json").exists()
    assert not (reviews_dir / "specialist_review_originality.md").exists()
    assert (reviews_dir / "specialist_review_significance.json").exists()
