from __future__ import annotations

from pathlib import Path

import fusion_reviewer.config as config_module
import fusion_reviewer.evidence as evidence_module


def test_export_pdf_report_omits_source_appendix_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "review_outputs"))
    config_module.get_settings.cache_clear()
    settings = config_module.get_settings()

    source_pdf = tmp_path / "source.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\nfake\n")
    report_pdf = tmp_path / "report.pdf"

    captured: dict[str, object] = {}

    def fake_build_source_annotations_for_export(*, annotations, content_list):
        captured["annotations_called"] = True
        return [{"page_number": 1}]

    def fake_build_review_report_pdf(**kwargs):
        captured["source_pdf_bytes"] = kwargs.get("source_pdf_bytes")
        captured["source_annotations"] = kwargs.get("source_annotations")
        return b"%PDF-1.4\nreport\n"

    monkeypatch.setattr(evidence_module, "build_source_annotations_for_export", fake_build_source_annotations_for_export)
    monkeypatch.setattr(evidence_module, "build_review_report_pdf", fake_build_review_report_pdf)

    ok = evidence_module.export_pdf_report(
        settings=settings,
        job_id="job-1",
        title="测试论文",
        source_name="source.pdf",
        source_pdf_path=source_pdf,
        final_markdown="# 审稿总报告\n\n内容",
        content_list=[{"page": 1, "text": "hello"}],
        annotations=[],
        token_usage={"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        agent_model="mock-model",
        report_pdf_path=report_pdf,
    )

    assert ok is True
    assert report_pdf.exists()
    assert captured["source_pdf_bytes"] is None
    assert captured["source_annotations"] == []
    assert "annotations_called" not in captured
