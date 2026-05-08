from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from fusion_reviewer import normalization as norm
from fusion_reviewer import document_io


def _write_sample_pdf(path: Path, lines: list[str]) -> None:
    c = canvas.Canvas(str(path), pagesize=letter)
    y = 750
    for line in lines:
        c.drawString(72, y, line)
        y -= 18
    c.save()


def _write_minimal_docx(path: Path, paragraphs: list[str]) -> None:
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(
        f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>" for paragraph in paragraphs
    )
    xml = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:document xmlns:w="{ns}">'
        f"<w:body>{body}<w:sectPr/></w:body>"
        f"</w:document>"
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)


def test_pdf_prefers_mineru_markdown_when_available(tmp_path, monkeypatch):
    pdf_path = tmp_path / "paper.pdf"
    _write_sample_pdf(pdf_path, ["MinerU should win", "Local fallback should still build snapshots"])

    fake_payload = {
        "markdown": "# MinerU Parsed Paper\n\n## Page 1\nMinerU evidence line",
        "content_list": [
            {"page_idx": 0, "type": "text", "text": "MinerU evidence line", "bbox": [1, 2, 3, 4]},
        ],
        "plain_text": "",
        "extractor_used": "mineru",
        "conversion_used": None,
    }
    monkeypatch.setattr(norm, "_try_mineru", lambda source, cfg: (fake_payload, None))

    result = norm.normalize_document(pdf_path, config=norm.NormalizationConfig(output_root=tmp_path / "review_outputs"))

    assert result.markdown.startswith("# MinerU Parsed Paper")
    assert result.page_index[1] == ["MinerU evidence line"]
    assert result.diagnostics["mineru_attempted"] is True
    assert result.diagnostics["mineru_succeeded"] is True
    assert result.artifacts.diagnostics_path.exists()
    diag = json.loads(result.artifacts.diagnostics_path.read_text(encoding="utf-8"))
    assert diag["artifact_paths"]["markdown_path"].endswith("normalized.md")


def test_pdf_local_fallback_creates_snapshots_and_cache(tmp_path, monkeypatch):
    pdf_path = tmp_path / "paper.pdf"
    _write_sample_pdf(pdf_path, ["Short text"])

    monkeypatch.setattr(norm, "_try_mineru", lambda source, cfg: (None, "mineru disabled"))
    config = norm.NormalizationConfig(
        output_root=tmp_path / "review_outputs",
        enable_mineru=False,
        allow_local_pdf_fallback=True,
        allow_ocr=False,
    )

    first = norm.normalize_document(pdf_path, config=config)
    second = norm.normalize_document(pdf_path, config=config)

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert "Short text" in first.markdown
    assert first.artifacts.manifest_path.exists()
    assert first.artifacts.diagnostics_path.exists()

    if document_io.fitz is not None:
        assert first.snapshot_paths, "Expected snapshot images for a low-text PDF page"
        for snapshot in first.snapshot_paths:
            assert snapshot.exists()


def test_docx_text_fallback_when_conversion_fails(tmp_path, monkeypatch):
    docx_path = tmp_path / "paper.docx"
    _write_minimal_docx(docx_path, ["Docx first paragraph", "Second paragraph from docx"])

    monkeypatch.setattr(norm, "detect_libreoffice_binary", lambda preferred=None: None)
    config = norm.NormalizationConfig(
        output_root=tmp_path / "review_outputs",
        allow_docx_text_fallback=True,
        enable_mineru=False,
    )

    result = norm.normalize_document(docx_path, config=config)

    assert result.layout_fidelity == "degraded"
    assert result.conversion_used == "docx-text-fallback"
    assert "Docx first paragraph" in result.plain_text
    assert "Second paragraph from docx" in result.plain_text
    assert result.page_index[1] == ["Docx first paragraph", "Second paragraph from docx"]
    assert result.diagnostics["source_kind"] == "docx"
    assert result.normalized_source_path.suffix.lower() == ".docx"
    assert result.artifacts.diagnostics_path.exists()


def test_docx_uses_native_text_when_pdf_extraction_is_garbled(tmp_path, monkeypatch):
    docx_path = tmp_path / "paper.docx"
    paragraphs = [
        "这是第一段中文内容，用来模拟较长的论文正文，以便质量检查逻辑能够识别出 PDF 抽取文本与原始 DOCX 文本之间的严重偏差。"
        * 2,
        "这是第二段中文内容，继续提供足够的中文字符数量，让自动降级逻辑可以稳定地判断当前 PDF 抽取结果已经不再适合作为共享证据。"
        * 2,
        "这是第三段中文内容，用于确保文本长度超过阈值，同时也更接近真实论文在引言、方法与讨论中的自然行文方式。"
        * 2,
    ]
    _write_minimal_docx(docx_path, paragraphs)
    fake_pdf = tmp_path / "converted.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4\n%fake\n")

    monkeypatch.setattr(norm, "detect_libreoffice_binary", lambda preferred=None: "soffice.exe")
    monkeypatch.setattr(norm, "convert_office_to_pdf", lambda *args, **kwargs: fake_pdf)
    monkeypatch.setattr(
        norm,
        "_normalize_pdf",
        lambda source, cfg: {
            "normalized_source_path": source,
            "markdown": "# Garbled\n\n## Page 1\n浣犲ソ 涓栫晫",
            "plain_text": "浣犲ソ 涓栫晫 ??? ???",
            "page_index": {1: ["浣犲ソ", "涓栫晫"]},
            "structured_pages": [{"page_number": 1, "text": "浣犲ソ 涓栫晫", "blocks": []}],
            "diagnostics": {"extractor_used": "fitz", "layout_fidelity": "full"},
            "snapshot_paths": [],
            "content_list": [{"page_idx": 0, "type": "text", "text": "浣犲ソ"}],
            "layout_fidelity": "full",
            "extractor_used": "fitz",
            "conversion_used": None,
            "warning": None,
        },
    )

    config = norm.NormalizationConfig(
        output_root=tmp_path / "review_outputs",
        enable_mineru=False,
        min_docx_chars_for_quality_check=100,
    )
    result = norm.normalize_document(docx_path, config=config)

    assert result.extractor_used == "docx-text"
    assert result.layout_fidelity == "degraded"
    assert result.conversion_used == "libreoffice->pdf+docx-text-authoritative"
    assert "这是第一段中文内容" in result.plain_text
    assert result.diagnostics["authoritative_evidence_source"] == "docx-native"
    assert result.diagnostics["pdf_extraction_quality_check"]["use_docx_text_fallback"] is True
    assert result.normalized_source_path.exists()


def test_doc_requires_libreoffice_or_word_com(tmp_path, monkeypatch):
    doc_path = tmp_path / "paper.doc"
    doc_path.write_bytes(b"not a real doc file")

    monkeypatch.setattr(norm, "detect_libreoffice_binary", lambda preferred=None: None)
    monkeypatch.setattr(norm, "convert_with_word_com", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("word com unavailable")))
    config = norm.NormalizationConfig(output_root=tmp_path / "review_outputs")

    try:
        norm.normalize_document(doc_path, config=config)
    except RuntimeError as exc:
        assert "Unable to normalize .doc file" in str(exc)
    else:
        raise AssertionError("Expected .doc normalization to fail when both LibreOffice and Word COM are unavailable")


def test_doc_falls_back_to_word_com_docx_path(tmp_path, monkeypatch):
    doc_path = tmp_path / "paper.doc"
    doc_path.write_bytes(b"fake-doc")
    converted_docx_template = tmp_path / "converted-template.docx"
    _write_minimal_docx(converted_docx_template, ["第一段", "第二段"])
    calls: list[str] = []

    monkeypatch.setattr(norm, "detect_libreoffice_binary", lambda preferred=None: None)

    def fake_word_com(source, output, *, target_kind, timeout_seconds):
        calls.append(target_kind)
        if target_kind == "pdf":
            raise RuntimeError("pdf export failed")
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(converted_docx_template, output)
        return output

    monkeypatch.setattr(norm, "convert_with_word_com", fake_word_com)
    config = norm.NormalizationConfig(
        output_root=tmp_path / "review_outputs",
        enable_mineru=False,
    )

    result = norm.normalize_document(doc_path, config=config)

    assert calls == ["pdf", "docx", "pdf"]
    assert result.extractor_used == "docx-text"
    assert result.conversion_used == "word-com->docx+docx-text-fallback"
    assert result.diagnostics["source_kind"] == "doc"
    assert result.normalized_source_path.suffix.lower() == ".docx"


def test_convert_office_to_pdf_tolerates_non_utf8_subprocess_output(tmp_path, monkeypatch):
    source_path = tmp_path / "paper.docx"
    source_path.write_text("placeholder", encoding="utf-8")
    output_dir = tmp_path / "out"
    pdf_path = output_dir / "paper.pdf"

    monkeypatch.setattr(document_io, "detect_libreoffice_binary", lambda preferred=None: "soffice.exe")

    def fake_run(command, capture_output, text, timeout, check, env=None):
        assert capture_output is True
        assert text is False
        output_dir.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(b"%PDF-1.4\n")
        return subprocess.CompletedProcess(command, 0, stdout="转换成功".encode("gbk"), stderr=b"")

    monkeypatch.setattr(document_io.subprocess, "run", fake_run)

    result = document_io.convert_office_to_pdf(source_path, output_dir, libreoffice_binary="soffice.exe")

    assert result == pdf_path


def test_convert_office_to_pdf_preserves_non_utf8_error_output(tmp_path, monkeypatch):
    source_path = tmp_path / "paper.docx"
    source_path.write_text("placeholder", encoding="utf-8")
    output_dir = tmp_path / "out"

    monkeypatch.setattr(document_io, "detect_libreoffice_binary", lambda preferred=None: "soffice.exe")

    def fake_run(command, capture_output, text, timeout, check, env=None):
        assert text is False
        return subprocess.CompletedProcess(command, 1, stdout=b"", stderr="格式错误".encode("gbk"))

    monkeypatch.setattr(document_io.subprocess, "run", fake_run)

    try:
        document_io.convert_office_to_pdf(source_path, output_dir, libreoffice_binary="soffice.exe")
    except RuntimeError as exc:
        assert "格式错误" in str(exc)
    else:
        raise AssertionError("Expected non-zero LibreOffice conversion to raise")
