"""Deterministic recovery and repair operations — no LLM/API calls."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_writer import write_json_atomic, write_text_atomic
from .models import AgentReview, EditorReport
from .text_utils import looks_garbled


def _timestamp_token() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")[:21]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# ---- backup ------------------------------------------------------------------

def backup_run_artifacts(run_dir: Path) -> Path:
    backup_dir = run_dir / "_repair_backups" / _timestamp_token()
    backup_targets = [
        "reviews_input.json", "editor_input.json",
        "meta_review.md", "meta_review.json",
        "final_report.md", "final_report.pdf",
        "concerns_table.csv", "concerns_table.json",
        "final_summary.json",
        "01-审稿总报告.md", "02-审稿总报告.pdf",
        "03-元审稿.md", "04-问题汇总.csv", "05-运行摘要.json",
        "91-reviewer输入汇总.json", "92-editor输入汇总.json",
        "evidence/normalized.md", "evidence/plain_text.txt",
        "evidence/page_index.json", "evidence/paragraph_index.json",
        "evidence/review_evidence_notes.json", "evidence/diagnostics.json",
    ]
    for relative_name in backup_targets:
        source = run_dir / relative_name
        if not source.exists():
            continue
        target = backup_dir / relative_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    # Keep only the latest 2 backups
    backups_parent = run_dir / "_repair_backups"
    existing = sorted(
        [d for d in backups_parent.iterdir() if d.is_dir()],
        key=lambda p: p.name,
        reverse=True,
    )
    for stale in existing[2:]:
        shutil.rmtree(stale)

    return backup_dir


# ---- shell corruption detection ----------------------------------------------

def find_shell_corruption(payload: object, *, path: str = "root") -> str | None:
    if isinstance(payload, str):
        if looks_garbled(payload):
            return path
        return None
    if isinstance(payload, dict):
        for key, value in payload.items():
            hit = find_shell_corruption(value, path=f"{path}.{key}")
            if hit:
                return hit
        return None
    if isinstance(payload, list):
        for idx, value in enumerate(payload):
            hit = find_shell_corruption(value, path=f"{path}[{idx}]")
            if hit:
                return hit
        return None
    return None


# ---- review recovery ---------------------------------------------------------

def load_recoverable_reviews(run_dir: Path) -> list[AgentReview]:
    """Try to recover reviewer JSON from individual files or combined file."""
    reviews = _load_reviews_from_individual_files(run_dir)
    if reviews:
        return reviews
    reviews = _load_reviews_from_combined_file(run_dir)
    if reviews:
        return reviews
    raise RuntimeError(
        "找不到可恢复的 reviewer JSON。"
        "repair-run 可以重建汇总文件，但前提是 run 目录里至少还保留一份未损坏的 reviewer JSON。"
    )


def _load_reviews_from_individual_files(run_dir: Path) -> list[AgentReview]:
    reviews_dir = run_dir / "reviews"
    if not reviews_dir.exists():
        return []
    reviews: list[AgentReview] = []
    for json_path in sorted(reviews_dir.glob("*.json")):
        try:
            raw = json_path.read_text(encoding="utf-8")
            if looks_garbled(raw):
                continue
            data = json.loads(raw)
            reviews.append(AgentReview.model_validate(data))
        except Exception:
            continue
    return reviews


def _load_reviews_from_combined_file(run_dir: Path) -> list[AgentReview]:
    combined_path = run_dir / "reviews_input.json"
    if not combined_path.exists():
        return []
    try:
        raw = combined_path.read_text(encoding="utf-8")
        if looks_garbled(raw):
            return []
        payload = json.loads(raw)
        if not isinstance(payload, list):
            return []
        return [AgentReview.model_validate(item) for item in payload]
    except Exception:
        return []


def load_editor_from_file(path: Path) -> EditorReport:
    raw = path.read_text(encoding="utf-8")
    if looks_garbled(raw):
        raise RuntimeError(f"Editor payload appears shell-corrupted: {path}")
    return EditorReport.model_validate(json.loads(raw))


# ---- DOCX evidence repair ----------------------------------------------------

def repair_docx_evidence_if_needed(run_dir: Path, *, force: bool = False) -> dict[str, Any]:
    """Replace garbled PDF-derived evidence with DOCX-native text when needed."""
    from .document_io import extract_docx_text
    from .normalization import (
        NormalizationConfig,
        _assess_docx_pdf_alignment,
        build_markdown_from_page_index,
    )

    evidence_dir = run_dir / "evidence"
    source_copy = next(iter(sorted(evidence_dir.glob("source_copy.docx"))), None)
    if source_copy is None:
        return {"applied": False, "reason": "no_docx_source"}

    docx_text = extract_docx_text(source_copy)
    if not docx_text.strip():
        return {"applied": False, "reason": "empty_docx_text"}

    current_plain_text = ""
    plain_text_path = evidence_dir / "plain_text.txt"
    if plain_text_path.exists():
        current_plain_text = plain_text_path.read_text(encoding="utf-8", errors="ignore")

    diagnostics_path = evidence_dir / "diagnostics.json"
    diagnostics = _read_json(diagnostics_path) if diagnostics_path.exists() else {}
    assessment = _assess_docx_pdf_alignment(
        docx_text,
        current_plain_text,
        NormalizationConfig(enable_mineru=False),
    )
    if not force and not assessment["use_docx_text_fallback"]:
        return {"applied": False, "reason": "existing_evidence_looks_ok", "quality_check": assessment}

    paragraph_index = _build_paragraph_index(docx_text)
    page_lines = [f"[PARA {item['paragraph']}] {item['text']}" for item in paragraph_index]
    normalized_markdown = build_markdown_from_page_index({1: page_lines}, title=source_copy.stem)

    write_text_atomic(evidence_dir / "normalized.md", normalized_markdown)
    write_text_atomic(evidence_dir / "plain_text.txt", "\n".join(page_lines).strip() + "\n")
    write_json_atomic(evidence_dir / "page_index.json", {1: page_lines})
    write_json_atomic(evidence_dir / "paragraph_index.json", paragraph_index)
    write_json_atomic(
        evidence_dir / "review_evidence_notes.json",
        {
            "authoritative_evidence": "normalized.md",
            "authoritative_evidence_source": "docx-native",
            "evidence_locator_scheme": "Use locators such as docx para 37.",
            "reason": "repair-run replaced garbled PDF-derived evidence with DOCX-native text.",
            "quality_check": assessment,
        },
    )

    diagnostics.update({
        "repair_run_applied": True,
        "repair_authoritative_evidence_source": "docx-native",
        "repair_quality_check": assessment,
        "layout_fidelity": "degraded",
        "extractor_used": "docx-text",
        "conversion_used": diagnostics.get("conversion_used") or "libreoffice->pdf+docx-text-authoritative",
    })
    write_json_atomic(diagnostics_path, diagnostics)
    return {
        "applied": True,
        "reason": "docx_native_evidence_restored",
        "paragraph_count": len(paragraph_index),
        "quality_check": assessment,
    }


def _build_paragraph_index(docx_text: str) -> list[dict[str, Any]]:
    paragraphs = [line.strip() for line in docx_text.splitlines() if line.strip()]
    return [
        {"paragraph": idx, "locator": f"docx para {idx}", "text": text}
        for idx, text in enumerate(paragraphs, start=1)
    ]
