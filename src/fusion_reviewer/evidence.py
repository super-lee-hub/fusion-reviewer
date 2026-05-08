from __future__ import annotations

import os
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from deepreview.report.review_report_pdf import build_review_report_pdf
from deepreview.report.source_annotations import build_source_annotations_for_export
from deepreview.types import AnnotationItem
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from .config import FusionSettings
from .models import Concern, EvidenceRef
from .normalization import NormalizationConfig, normalize_document
from .storage import ensure_artifact_paths, write_json_atomic, write_text_atomic


@dataclass
class EvidenceWorkspace:
    markdown: str
    plain_text: str
    content_list: list[dict[str, Any]] | None
    page_index: dict[int, list[str]]
    extractor_used: str
    layout_fidelity: str
    conversion_used: str | None = None
    warning: str | None = None
    normalized_source_path: Path | None = None
    diagnostics: dict[str, Any] | None = None
    snapshot_paths: list[Path] | None = None


def _normalization_config(settings: FusionSettings) -> NormalizationConfig:
    return NormalizationConfig(
        output_root=settings.data_dir / settings.preprocess_cache_dirname,
        enable_mineru=True,
        allow_local_pdf_fallback=settings.allow_local_parse_fallback,
        allow_pdf_text_fallback=True,
        allow_ocr=True,
        max_snapshot_pages=settings.max_snapshot_pages,
        libreoffice_binary=settings.libreoffice_bin,
        mineru_base_url=settings.mineru_base_url,
        mineru_api_token=settings.mineru_api_token,
        mineru_model_version=settings.mineru_model_version,
        mineru_upload_endpoint=settings.mineru_upload_endpoint,
        mineru_poll_endpoint_templates=tuple(settings.mineru_poll_templates()),
        mineru_poll_interval_seconds=settings.mineru_poll_interval_seconds,
        mineru_poll_timeout_seconds=settings.mineru_poll_timeout_seconds,
        mineru_request_max_retries=settings.mineru_request_max_retries,
        mineru_retry_backoff_seconds=settings.mineru_retry_backoff_seconds,
    )


def prepare_document_once(job_id: str, source_document: Path, settings: FusionSettings) -> EvidenceWorkspace:
    normalized = normalize_document(source_document, config=_normalization_config(settings))
    paths = ensure_artifact_paths(job_id)

    write_text_atomic(paths["normalized_markdown"], normalized.markdown)
    write_text_atomic(paths["plain_text"], normalized.plain_text)
    write_json_atomic(paths["page_index"], normalized.page_index)
    write_json_atomic(
        paths["structured_content"],
        {"pages": normalized.structured_pages, "content_list": normalized.content_list or []},
    )

    run_snapshot_paths: list[str] = []
    if paths["snapshots_dir"].exists():
        for existing in paths["snapshots_dir"].glob("*"):
            if existing.is_file():
                existing.unlink()
    for snapshot in normalized.snapshot_paths:
        if not snapshot.exists():
            continue
        target = paths["snapshots_dir"] / snapshot.name
        shutil.copy2(snapshot, target)
        run_snapshot_paths.append(str(target))

    diagnostics = {
        **(normalized.diagnostics or {}),
        "cache_hit": normalized.cache_hit,
        "cache_key": normalized.cache_key,
        "document_kind": normalized.document_kind,
        "run_snapshot_paths": run_snapshot_paths,
    }
    write_json_atomic(paths["diagnostics"], diagnostics)

    normalized_pdf_path: Path | None = None
    if normalized.normalized_source_path.exists() and normalized.normalized_source_path.suffix.lower() == ".pdf":
        shutil.copy2(normalized.normalized_source_path, paths["normalized_pdf"])
        normalized_pdf_path = paths["normalized_pdf"]
    elif paths["normalized_pdf"].exists():
        paths["normalized_pdf"].unlink()

    return EvidenceWorkspace(
        markdown=normalized.markdown,
        plain_text=normalized.plain_text,
        content_list=normalized.content_list,
        page_index=normalized.page_index,
        extractor_used=normalized.extractor_used,
        layout_fidelity=normalized.layout_fidelity,
        conversion_used=normalized.conversion_used,
        warning=normalized.warning,
        normalized_source_path=normalized_pdf_path or normalized.normalized_source_path,
        diagnostics=diagnostics,
        snapshot_paths=[Path(item) for item in run_snapshot_paths],
    )


def serialize_page_index(page_index: dict[int, list[str]], max_chars: int) -> str:
    parts: list[str] = []
    budget = max_chars
    for page in sorted(page_index):
        header = f"\n## Page {page}\n"
        if len(header) > budget:
            break
        parts.append(header)
        budget -= len(header)
        for line_no, text in enumerate(page_index[page], start=1):
            row = f"[P{page} L{line_no}] {text}\n"
            if len(row) > budget:
                return "".join(parts).strip()
            parts.append(row)
            budget -= len(row)
    return "".join(parts).strip()


def quote_for_span(page_index: dict[int, list[str]], page: int | None, start_line: int | None, end_line: int | None) -> str:
    if page is None:
        return ""
    lines = page_index.get(page, [])
    if not lines:
        return ""
    start_idx = max(0, (start_line or 1) - 1)
    end_idx = min(len(lines), end_line or start_idx + 1)
    return "\n".join(lines[start_idx:end_idx]).strip()


def seed_evidence_refs(page_index: dict[int, list[str]], *, limit: int = 6) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for page in sorted(page_index):
        lines = page_index[page]
        if not lines:
            continue
        chunk_size = 2
        for index in range(0, min(len(lines), 8), chunk_size):
            start = index + 1
            end = min(len(lines), index + chunk_size)
            quote = quote_for_span(page_index, page, start, end)
            if quote:
                refs.append(
                    {
                        "page": page,
                        "start_line": start,
                        "end_line": end,
                        "quote": quote,
                        "locator": f"p.{page} lines {start}-{end}",
                    }
                )
            if len(refs) >= limit:
                return refs
    if not refs:
        refs.append(
            {
                "page": 1,
                "start_line": 1,
                "end_line": 1,
                "quote": "No evidence could be extracted from the document text.",
                "locator": "p.1 lines 1-1",
            }
        )
    return refs


def concerns_to_annotations(concerns: list[Concern]) -> list[AnnotationItem]:
    annotations: list[AnnotationItem] = []
    for concern in concerns:
        if not concern.evidence_refs:
            continue
        ref = next((item for item in concern.evidence_refs if item.page is not None), None)
        if ref is None:
            continue
        annotations.append(
            AnnotationItem(
                id=concern.id,
                page=ref.page or 1,
                start_line=ref.start_line or 1,
                end_line=ref.end_line or ref.start_line or 1,
                text=ref.quote,
                comment=concern.description,
                summary=concern.title,
                object_type="issue",
                severity=concern.severity,
            )
        )
    return annotations


@contextmanager
def _pushd(path: Path):
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def export_pdf_report(
    *,
    settings: FusionSettings,
    job_id: str,
    title: str,
    source_name: str,
    source_pdf_path: Path,
    final_markdown: str,
    content_list: list[dict[str, Any]] | None,
    annotations: list[AnnotationItem],
    token_usage: dict[str, int],
    agent_model: str,
    report_pdf_path: Path,
) -> bool:
    if not source_pdf_path.exists() or source_pdf_path.suffix.lower() != ".pdf":
        return False
    try:
        include_source_appendix = bool(settings.attach_source_pdf_appendix)
        with _pushd(settings.deepreview_root):
            source_annotations = (
                build_source_annotations_for_export(
                    annotations=annotations,
                    content_list=content_list,
                )
                if include_source_appendix
                else []
            )
            pdf_bytes = build_review_report_pdf(
                workspace_title=title,
                source_pdf_name=source_name,
                run_id=job_id,
                status="completed",
                decision=None,
                estimated_cost=0,
                actual_cost=None,
                exported_at=datetime.now(timezone.utc),
                meta_review={},
                reviewers=[],
                raw_output=None,
                final_report_markdown=final_markdown,
                source_pdf_bytes=source_pdf_path.read_bytes() if include_source_appendix else None,
                source_annotations=source_annotations,
                review_display_id=None,
                owner_email=None,
                token_usage=token_usage,
                agent_model=agent_model,
            )
        report_pdf_path.write_bytes(pdf_bytes)
        return True
    except Exception:
        _export_basic_pdf(final_markdown, report_pdf_path)
        return True


def _export_basic_pdf(markdown: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(output_path), pagesize=A4)
    styles = getSampleStyleSheet()
    story = []
    for block in (markdown or "").splitlines():
        text = block.strip()
        if not text:
            story.append(Spacer(1, 8))
            continue
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        story.append(Paragraph(safe, styles["BodyText"]))
        story.append(Spacer(1, 6))
    doc.build(story)


def evidence_ref_to_text(ref: EvidenceRef) -> str:
    if ref.locator:
        return ref.locator
    if ref.page is None:
        return ref.quote.strip()
    start = ref.start_line or 1
    end = ref.end_line or start
    return f"p.{ref.page} lines {start}-{end}"
