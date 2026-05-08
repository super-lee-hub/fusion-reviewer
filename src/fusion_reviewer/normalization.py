from __future__ import annotations

import json
from difflib import SequenceMatcher
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .document_io import (
    ArtifactPaths,
    DocumentKind,
    LayoutFidelity,
    NormalizedDocument,
    PageRecord,
    build_artifact_paths,
    build_markdown_from_page_index,
    build_page_index_from_content_list,
    build_page_index_from_pages,
    build_plain_text_from_page_index,
    compute_cache_key,
    convert_office_to_pdf,
    convert_with_word_com,
    detect_document_kind,
    detect_libreoffice_binary,
    ensure_output_root,
    extract_docx_text,
    fitz,
    is_cache_fresh,
    load_cached_document,
    make_page_record,
    save_normalized_document,
)

try:  # pragma: no cover - optional dependency
    from pypdf import PdfReader  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    PdfReader = None  # type: ignore


@dataclass(slots=True)
class NormalizationConfig:
    output_root: Path = field(default_factory=lambda: Path.cwd() / "review_outputs")
    enable_mineru: bool = True
    allow_local_pdf_fallback: bool = True
    allow_pdf_text_fallback: bool = True
    allow_ocr: bool = True
    ocr_languages: str = "eng"
    ocr_dpi: int = 300
    low_quality_threshold: int = 80
    scanned_threshold: int = 50
    max_snapshot_pages: int = 4
    libreoffice_binary: str | None = None
    libreoffice_timeout_seconds: int = 120
    allow_docx_text_fallback: bool = True
    mineru_base_url: str = "https://mineru.net/api/v4"
    mineru_api_token: str | None = None
    mineru_model_version: str = "vlm"
    mineru_upload_endpoint: str = "/file-urls/batch"
    mineru_poll_endpoint_templates: tuple[str, ...] = (
        "/extract-results/batch/{batch_id}",
        "/extract-results/{batch_id}",
        "/extract/task/{batch_id}",
    )
    mineru_poll_interval_seconds: float = 3.0
    mineru_poll_timeout_seconds: int = 900
    mineru_request_max_retries: int = 2
    mineru_retry_backoff_seconds: float = 1.5
    docx_pdf_alignment_threshold: float = 0.35
    min_docx_chars_for_quality_check: int = 400
    normalization_version: str = "2026-03-27-docx-quality-guard"

    def signature(self) -> str:
        payload = {
            "enable_mineru": self.enable_mineru,
            "allow_local_pdf_fallback": self.allow_local_pdf_fallback,
            "allow_pdf_text_fallback": self.allow_pdf_text_fallback,
            "allow_ocr": self.allow_ocr,
            "ocr_languages": self.ocr_languages,
            "ocr_dpi": self.ocr_dpi,
            "low_quality_threshold": self.low_quality_threshold,
            "scanned_threshold": self.scanned_threshold,
            "max_snapshot_pages": self.max_snapshot_pages,
            "libreoffice_binary": self.libreoffice_binary,
            "libreoffice_timeout_seconds": self.libreoffice_timeout_seconds,
            "allow_docx_text_fallback": self.allow_docx_text_fallback,
            "mineru_base_url": self.mineru_base_url,
            "mineru_api_token": bool(self.mineru_api_token),
            "mineru_model_version": self.mineru_model_version,
            "mineru_upload_endpoint": self.mineru_upload_endpoint,
            "mineru_poll_endpoint_templates": list(self.mineru_poll_endpoint_templates),
            "mineru_poll_interval_seconds": self.mineru_poll_interval_seconds,
            "mineru_poll_timeout_seconds": self.mineru_poll_timeout_seconds,
            "mineru_request_max_retries": self.mineru_request_max_retries,
            "mineru_retry_backoff_seconds": self.mineru_retry_backoff_seconds,
            "docx_pdf_alignment_threshold": self.docx_pdf_alignment_threshold,
            "min_docx_chars_for_quality_check": self.min_docx_chars_for_quality_check,
            "normalization_version": self.normalization_version,
        }
        return json.dumps(payload, sort_keys=True, ensure_ascii=True)


def normalize_document(
    source_path: str | Path,
    *,
    config: NormalizationConfig | None = None,
    output_root: str | Path | None = None,
) -> NormalizedDocument:
    cfg = config or NormalizationConfig()
    if output_root is not None:
        cfg = replace(cfg, output_root=Path(output_root))

    source = Path(source_path)
    if not source.exists():
        raise FileNotFoundError(f"Document not found: {source}")

    ensure_output_root(cfg.output_root)
    kind = detect_document_kind(source)
    cache_key = compute_cache_key(source, salt=cfg.signature())
    artifacts = build_artifact_paths(cfg.output_root, source, cache_key)

    cached = load_cached_document(source, kind, cache_key, artifacts)
    if cached is not None:
        return cached

    if kind == "pdf":
        payload = _normalize_pdf(source, cfg)
    elif kind == "docx":
        payload = _normalize_docx(source, cfg)
    else:
        payload = _normalize_doc(source, cfg)

    return save_normalized_document(
        source_path=source,
        document_kind=kind,
        cache_key=cache_key,
        artifacts=artifacts,
        normalized_source_path=payload["normalized_source_path"],
        markdown=payload["markdown"],
        plain_text=payload["plain_text"],
        page_index=payload["page_index"],
        structured_pages=payload["structured_pages"],
        diagnostics=payload["diagnostics"],
        snapshot_paths=payload["snapshot_paths"],
        content_list=payload["content_list"],
        layout_fidelity=payload["layout_fidelity"],
        extractor_used=payload["extractor_used"],
        conversion_used=payload["conversion_used"],
        warning=payload["warning"],
    )


def _normalize_pdf(source: Path, cfg: NormalizationConfig) -> dict[str, Any]:
    mineru_payload = None
    mineru_warning: str | None = None
    if cfg.enable_mineru:
        mineru_payload, mineru_warning = _try_mineru(source, cfg)

    local_payload = _analyze_pdf_locally(source, cfg)
    page_index = local_payload["page_index"]
    content_list = mineru_payload.get("content_list") if mineru_payload else local_payload["content_list"]
    if not content_list:
        content_list = local_payload["content_list"]
    mineru_page_index = build_page_index_from_content_list(content_list if mineru_payload else None)
    if mineru_page_index:
        page_index = mineru_page_index
    if mineru_payload and mineru_payload.get("markdown"):
        markdown = mineru_payload["markdown"]
    else:
        markdown = build_markdown_from_page_index(page_index, title=source.stem)

    plain_text = mineru_payload.get("plain_text") if mineru_payload else local_payload["plain_text"]
    if not plain_text:
        plain_text = build_plain_text_from_page_index(page_index)

    extractor_used = mineru_payload.get("extractor_used") if mineru_payload else local_payload["extractor_used"]
    conversion_used = mineru_payload.get("conversion_used") if mineru_payload else local_payload["conversion_used"]
    warning = mineru_warning or local_payload["warning"]
    layout_fidelity = "full" if mineru_payload else local_payload["layout_fidelity"]
    diagnostics = {
        **local_payload["diagnostics"],
        "mineru_attempted": cfg.enable_mineru,
        "mineru_succeeded": bool(mineru_payload),
        "mineru_warning": mineru_warning,
    }
    if mineru_payload:
        diagnostics["mineru_payload"] = {
            "content_list_items": len(content_list or []),
            "has_markdown": bool(mineru_payload.get("markdown")),
        }
    return {
        "normalized_source_path": source,
        "markdown": markdown,
        "plain_text": plain_text,
        "page_index": page_index,
        "structured_pages": local_payload["structured_pages"],
        "diagnostics": diagnostics,
        "snapshot_paths": local_payload["snapshot_paths"],
        "content_list": content_list,
        "layout_fidelity": layout_fidelity,
        "extractor_used": extractor_used,
        "conversion_used": conversion_used,
        "warning": warning,
    }


def _try_mineru(source: Path, cfg: NormalizationConfig) -> tuple[dict[str, Any] | None, str | None]:
    try:
        from deepreview.adapters.mineru import MineruAdapter, MineruConfig  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency path
        return None, f"MinerU unavailable: {exc}"

    adapter = MineruAdapter(
        MineruConfig(
            base_url=cfg.mineru_base_url,
            api_token=cfg.mineru_api_token,
            model_version=cfg.mineru_model_version,
            upload_endpoint=cfg.mineru_upload_endpoint,
            poll_endpoint_templates=list(cfg.mineru_poll_endpoint_templates),
            poll_interval_seconds=cfg.mineru_poll_interval_seconds,
            poll_timeout_seconds=cfg.mineru_poll_timeout_seconds,
            allow_local_fallback=False,
            request_max_retries=cfg.mineru_request_max_retries,
            retry_backoff_seconds=cfg.mineru_retry_backoff_seconds,
        )
    )
    try:
        result = __import__("asyncio").run(adapter.parse_pdf(pdf_path=source, data_id=source.stem))
    except Exception as exc:
        return None, f"MinerU attempt failed: {type(exc).__name__}: {exc}"
    markdown = getattr(result, "markdown", "") or ""
    content_list = getattr(result, "content_list", None)
    return (
        {
            "markdown": markdown,
            "content_list": content_list,
            "plain_text": "",
            "extractor_used": "mineru",
            "conversion_used": None,
        },
        getattr(result, "warning", None),
    )


def _normalize_docx(source: Path, cfg: NormalizationConfig) -> dict[str, Any]:
    converted_pdf: Path | None = None
    warning: str | None = None
    conversion_used: str | None = None
    cache_key = compute_cache_key(source, salt=cfg.signature())
    work_dir = cfg.output_root / source.stem / cache_key / "conversion"
    docx_text = extract_docx_text(source)
    try:
        binary = detect_libreoffice_binary(cfg.libreoffice_binary)
        if not binary:
            raise FileNotFoundError("LibreOffice binary not found")
        converted_pdf = convert_office_to_pdf(
            source,
            work_dir,
            libreoffice_binary=binary,
            timeout_seconds=cfg.libreoffice_timeout_seconds,
        )
        conversion_used = "libreoffice->pdf"
        pdf_payload = _normalize_pdf(converted_pdf, cfg)
        quality_assessment = _assess_docx_pdf_alignment(
            docx_text,
            pdf_payload["plain_text"],
            cfg,
        )
        if quality_assessment["use_docx_text_fallback"]:
            fallback_payload = _build_docx_text_fallback_payload(
                source=source,
                docx_text=docx_text,
                warning=(
                    "DOCX was converted to PDF successfully, but the extracted PDF text did not align "
                    "with the original DOCX text closely enough. Falling back to DOCX-native text "
                    "for reviewer evidence."
                ),
            )
            fallback_payload["normalized_source_path"] = converted_pdf
            fallback_payload["conversion_used"] = "libreoffice->pdf+docx-text-authoritative"
            fallback_payload["diagnostics"] = {
                **fallback_payload["diagnostics"],
                "source_kind": "docx",
                "conversion_used": conversion_used,
                "converted_pdf_path": str(converted_pdf),
                "pdf_extraction_quality_check": quality_assessment,
                "pdf_extraction_diagnostics": pdf_payload["diagnostics"],
                "authoritative_evidence_source": "docx-native",
            }
            return fallback_payload
        pdf_payload["normalized_source_path"] = converted_pdf
        pdf_payload["conversion_used"] = conversion_used
        pdf_payload["diagnostics"] = {
            **pdf_payload["diagnostics"],
            "source_kind": "docx",
            "conversion_used": conversion_used,
            "converted_pdf_path": str(converted_pdf),
            "pdf_extraction_quality_check": quality_assessment,
            "authoritative_evidence_source": "pdf-extraction",
        }
        return pdf_payload
    except Exception as exc:
        warning = f"DOCX conversion failed: {type(exc).__name__}: {exc}"
        try:
            converted_pdf = convert_with_word_com(
                source,
                work_dir / f"{source.stem}.pdf",
                target_kind="pdf",
                timeout_seconds=cfg.libreoffice_timeout_seconds,
            )
            conversion_used = "word-com->pdf"
            pdf_payload = _normalize_pdf(converted_pdf, cfg)
            quality_assessment = _assess_docx_pdf_alignment(
                docx_text,
                pdf_payload["plain_text"],
                cfg,
            )
            if quality_assessment["use_docx_text_fallback"]:
                fallback_payload = _build_docx_text_fallback_payload(
                    source=source,
                    docx_text=docx_text,
                    warning=(
                        "DOCX was exported through Word COM, but the extracted PDF text did not align "
                        "with the original DOCX text closely enough. Falling back to DOCX-native text "
                        "for reviewer evidence."
                    ),
                )
                fallback_payload["normalized_source_path"] = converted_pdf
                fallback_payload["conversion_used"] = "word-com->pdf+docx-text-authoritative"
                fallback_payload["diagnostics"] = {
                    **fallback_payload["diagnostics"],
                    "source_kind": "docx",
                    "conversion_used": conversion_used,
                    "converted_pdf_path": str(converted_pdf),
                    "pdf_extraction_quality_check": quality_assessment,
                    "pdf_extraction_diagnostics": pdf_payload["diagnostics"],
                    "authoritative_evidence_source": "docx-native",
                    "warning": warning,
                }
                return fallback_payload
            pdf_payload["normalized_source_path"] = converted_pdf
            pdf_payload["conversion_used"] = conversion_used
            pdf_payload["diagnostics"] = {
                **pdf_payload["diagnostics"],
                "source_kind": "docx",
                "conversion_used": conversion_used,
                "converted_pdf_path": str(converted_pdf),
                "pdf_extraction_quality_check": quality_assessment,
                "authoritative_evidence_source": "pdf-extraction",
                "warning": warning,
            }
            return pdf_payload
        except Exception:
            if not cfg.allow_docx_text_fallback:
                raise RuntimeError(f"{warning}. Enable docx text fallback or install LibreOffice.") from exc

    if not docx_text.strip():
        raise RuntimeError("DOCX text fallback produced empty text")
    return _build_docx_text_fallback_payload(source=source, docx_text=docx_text, warning=warning)


def _normalize_doc(source: Path, cfg: NormalizationConfig) -> dict[str, Any]:
    cache_key = compute_cache_key(source, salt=cfg.signature())
    work_dir = cfg.output_root / source.stem / cache_key / "conversion"
    binary = detect_libreoffice_binary(cfg.libreoffice_binary)
    if binary:
        try:
            converted_pdf = convert_office_to_pdf(
                source,
                work_dir,
                libreoffice_binary=binary,
                timeout_seconds=cfg.libreoffice_timeout_seconds,
            )
            pdf_payload = _normalize_pdf(converted_pdf, cfg)
            pdf_payload["normalized_source_path"] = converted_pdf
            pdf_payload["conversion_used"] = "libreoffice->pdf"
            pdf_payload["diagnostics"] = {
                **pdf_payload["diagnostics"],
                "source_kind": "doc",
                "converted_pdf_path": str(converted_pdf),
            }
            return pdf_payload
        except Exception as libreoffice_exc:
            libreoffice_warning = f"LibreOffice .doc conversion failed: {type(libreoffice_exc).__name__}: {libreoffice_exc}"
        else:
            libreoffice_warning = None
    else:
        libreoffice_warning = "LibreOffice binary not found"

    try:
        converted_pdf = convert_with_word_com(
            source,
            work_dir / f"{source.stem}.pdf",
            target_kind="pdf",
            timeout_seconds=cfg.libreoffice_timeout_seconds,
        )
        pdf_payload = _normalize_pdf(converted_pdf, cfg)
        pdf_payload["normalized_source_path"] = converted_pdf
        pdf_payload["conversion_used"] = "word-com->pdf"
        pdf_payload["diagnostics"] = {
            **pdf_payload["diagnostics"],
            "source_kind": "doc",
            "converted_pdf_path": str(converted_pdf),
            "warning": libreoffice_warning,
        }
        return pdf_payload
    except Exception as word_pdf_exc:
        word_pdf_warning = f"Word COM .doc->pdf conversion failed: {type(word_pdf_exc).__name__}: {word_pdf_exc}"

    try:
        converted_docx = convert_with_word_com(
            source,
            work_dir / f"{source.stem}.docx",
            target_kind="docx",
            timeout_seconds=cfg.libreoffice_timeout_seconds,
        )
    except Exception as word_docx_exc:
        raise RuntimeError(
            "Unable to normalize .doc file. "
            f"{libreoffice_warning}. {word_pdf_warning}. "
            f"Word COM .doc->docx conversion failed: {type(word_docx_exc).__name__}: {word_docx_exc}"
        ) from word_docx_exc

    payload = _normalize_docx(converted_docx, cfg)
    payload["conversion_used"] = f"word-com->docx+{payload.get('conversion_used') or 'docx-text-fallback'}"
    payload["warning"] = f"{libreoffice_warning}. {word_pdf_warning}"
    payload["diagnostics"] = {
        **payload["diagnostics"],
        "source_kind": "doc",
        "conversion_used": payload["conversion_used"],
        "warning": payload["warning"],
        "converted_docx_path": str(converted_docx),
    }
    return payload


def _build_docx_text_fallback_payload(*, source: Path, docx_text: str, warning: str | None) -> dict[str, Any]:
    page_index = {1: [line.strip() for line in docx_text.splitlines() if line.strip()]}
    structured_pages = [
        {
            "page_number": 1,
            "text": docx_text,
            "blocks": [],
            "source": "docx-text-fallback",
        }
    ]
    markdown = build_markdown_from_page_index(page_index, title=source.stem)
    diagnostics = {
        "source_kind": "docx",
        "source_mode": "text_fallback",
        "layout_fidelity": "degraded",
        "extractor_used": "docx-text",
        "conversion_used": "docx-text-fallback",
        "warning": warning,
        "snapshot_paths": [],
        "authoritative_evidence_source": "docx-native",
    }
    return {
        "normalized_source_path": source,
        "markdown": markdown,
        "plain_text": docx_text,
        "page_index": page_index,
        "structured_pages": structured_pages,
        "diagnostics": diagnostics,
        "snapshot_paths": [],
        "content_list": [{"page_idx": 0, "type": "text", "text": line} for line in page_index[1]],
        "layout_fidelity": "degraded",
        "extractor_used": "docx-text",
        "conversion_used": "docx-text-fallback",
        "warning": warning,
    }


def _normalize_alignment_text(value: str) -> str:
    return "".join(ch for ch in value if not ch.isspace())


def _count_cjk_chars(value: str) -> int:
    return sum(1 for ch in value if "\u4e00" <= ch <= "\u9fff")


def _assess_docx_pdf_alignment(
    docx_text: str,
    extracted_text: str,
    cfg: NormalizationConfig,
) -> dict[str, Any]:
    source_text = _normalize_alignment_text(docx_text)
    candidate_text = _normalize_alignment_text(extracted_text)
    source_chars = len(source_text)
    source_cjk = _count_cjk_chars(source_text)
    candidate_cjk = _count_cjk_chars(candidate_text)
    compare_source = source_text[:8000]
    compare_candidate = candidate_text[:8000]
    similarity = (
        SequenceMatcher(a=compare_source, b=compare_candidate).ratio()
        if compare_source and compare_candidate
        else 0.0
    )
    has_replacement_markers = "\ufffd" in extracted_text or extracted_text.count("?") > max(25, len(extracted_text) // 30)
    use_fallback = (
        source_chars >= cfg.min_docx_chars_for_quality_check
        and (
            similarity < cfg.docx_pdf_alignment_threshold
            or (source_cjk >= 200 and candidate_cjk < max(80, source_cjk // 5))
            or has_replacement_markers
        )
    )
    return {
        "source_chars": source_chars,
        "source_cjk_chars": source_cjk,
        "extracted_chars": len(candidate_text),
        "extracted_cjk_chars": candidate_cjk,
        "similarity": round(similarity, 4),
        "threshold": cfg.docx_pdf_alignment_threshold,
        "has_replacement_markers": has_replacement_markers,
        "use_docx_text_fallback": use_fallback,
    }


def _analyze_pdf_locally(source: Path, cfg: NormalizationConfig) -> dict[str, Any]:
    if fitz is None:
        return _analyze_pdf_with_pypdf(source)

    page_records: list[PageRecord] = []
    structured_pages: list[dict[str, Any]] = []
    snapshot_paths: list[Path] = []
    all_text_lines: list[list[str]] = []

    doc = fitz.open(str(source))
    try:
        for page_number in range(doc.page_count):
            page = doc.load_page(page_number)
            raw_text = _safe_text(page.get_text("text"))
            block_items = _extract_fitz_blocks(page)
            images = len(page.get_images(full=True))
            scanned_candidate = len(raw_text.strip()) < cfg.scanned_threshold and images > 0
            used_ocr = False
            effective_text = raw_text
            if cfg.allow_ocr and scanned_candidate:
                ocr_text = _try_page_ocr(page, cfg)
                if ocr_text.strip():
                    effective_text = ocr_text
                    used_ocr = True
            line_candidates = [line.strip() for line in effective_text.splitlines() if line.strip()]
            if not line_candidates:
                for block in block_items:
                    text = str(block.get("text") or "").strip()
                    if text:
                        line_candidates.extend([line.strip() for line in text.splitlines() if line.strip()])
            low_quality = len(" ".join(line_candidates).strip()) < cfg.low_quality_threshold
            page_records.append(
                make_page_record(
                    page_number=page_number + 1,
                    text="\n".join(line_candidates).strip() or effective_text.strip(),
                    blocks=block_items,
                    images=images,
                    used_ocr=used_ocr,
                    scanned_candidate=scanned_candidate,
                    low_quality=low_quality,
                )
            )
            structured_pages.append(
                {
                    "page_number": page_number + 1,
                    "text": "\n".join(line_candidates).strip() or effective_text.strip(),
                    "blocks": block_items,
                    "images": images,
                    "used_ocr": used_ocr,
                    "scanned_candidate": scanned_candidate,
                    "low_quality": low_quality,
                }
            )
            all_text_lines.append(line_candidates or [effective_text.strip()])
            if (scanned_candidate or low_quality) and len(snapshot_paths) < cfg.max_snapshot_pages:
                snapshot_path = cfg.output_root / source.stem / compute_cache_key(source, salt=cfg.signature()) / "snapshots" / f"page-{page_number + 1:03d}.png"
                snapshot_path.parent.mkdir(parents=True, exist_ok=True)
                _render_page_snapshot(page, snapshot_path)
                snapshot_paths.append(snapshot_path)
    finally:
        doc.close()

    page_index = build_page_index_from_pages(all_text_lines)
    plain_text = "\n".join(
        [f"--- Page {page} ---\n" + "\n".join(page_index[page]) for page in sorted(page_index)]
    ).strip()
    content_list = _page_records_to_content_list(page_records)
    return {
        "page_records": page_records,
        "structured_pages": structured_pages,
        "page_index": page_index,
        "plain_text": plain_text,
        "content_list": content_list,
        "snapshot_paths": snapshot_paths,
        "layout_fidelity": "full",
        "extractor_used": "fitz",
        "conversion_used": None,
        "warning": None,
        "diagnostics": _page_record_diagnostics(page_records),
    }


def _analyze_pdf_with_pypdf(source: Path) -> dict[str, Any]:
    if PdfReader is None:
        raise RuntimeError("Neither fitz nor pypdf is available for PDF extraction")
    reader = PdfReader(str(source))
    page_index: dict[int, list[str]] = {}
    structured_pages: list[dict[str, Any]] = []
    content_list: list[dict[str, Any]] = []
    for page_number, page in enumerate(reader.pages, start=1):
        raw_text = _safe_text(page.extract_text() or "")
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        page_index[page_number] = lines or ([raw_text.strip()] if raw_text.strip() else [])
        structured_pages.append(
            {
                "page_number": page_number,
                "text": raw_text,
                "blocks": [],
                "images": 0,
                "used_ocr": False,
                "scanned_candidate": False,
                "low_quality": len(raw_text.strip()) < 80,
            }
        )
        content_list.extend(
            {
                "page_idx": page_number - 1,
                "type": "text",
                "text": line,
            }
            for line in lines
        )
    plain_text = build_plain_text_from_page_index(page_index)
    diagnostics = {
        "source_kind": "pdf",
        "source_mode": "pypdf_fallback",
        "layout_fidelity": "text_only",
        "extractor_used": "pypdf",
        "conversion_used": None,
        "snapshot_paths": [],
    }
    return {
        "page_records": [],
        "structured_pages": structured_pages,
        "page_index": page_index,
        "plain_text": plain_text,
        "content_list": content_list,
        "snapshot_paths": [],
        "layout_fidelity": "text_only",
        "extractor_used": "pypdf",
        "conversion_used": None,
        "warning": "PDF extracted with pypdf fallback",
        "diagnostics": diagnostics,
    }


def _extract_fitz_blocks(page: Any) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    try:
        raw_blocks = page.get_text("blocks")
    except Exception:
        raw_blocks = []
    for item in raw_blocks or []:
        if not isinstance(item, tuple) or len(item) < 5:
            continue
        x0, y0, x1, y1, text = item[:5]
        text = _safe_text(str(text))
        if not text.strip():
            continue
        blocks.append(
            {
                "bbox": [float(x0), float(y0), float(x1), float(y1)],
                "text": text.strip(),
            }
        )
    return blocks


def _page_records_to_content_list(records: list[PageRecord]) -> list[dict[str, Any]]:
    content_list: list[dict[str, Any]] = []
    for record in records:
        if record.blocks:
            for block in record.blocks:
                content_list.append(
                    {
                        "page_idx": record.page_number - 1,
                        "type": "text",
                        "text": str(block.get("text") or "").strip(),
                        "bbox": block.get("bbox"),
                    }
                )
        elif record.text.strip():
            for line in record.text.splitlines():
                if line.strip():
                    content_list.append(
                        {
                            "page_idx": record.page_number - 1,
                            "type": "text",
                            "text": line.strip(),
                        }
                    )
    return content_list


def _page_record_diagnostics(records: list[PageRecord]) -> dict[str, Any]:
    return {
        "page_diagnostics": [
            {
                "page_number": record.page_number,
                "text_length": len(record.text.strip()),
                "image_count": record.images,
                "scanned_candidate": record.scanned_candidate,
                "used_ocr": record.used_ocr,
                "low_quality": record.low_quality,
            }
            for record in records
        ]
    }


def _render_page_snapshot(page: Any, snapshot_path: Path) -> None:
    try:
        matrix = fitz.Matrix(2, 2) if fitz is not None else None
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        pix.save(str(snapshot_path))
    except Exception:
        snapshot_path.write_bytes(b"")


def _try_page_ocr(page: Any, cfg: NormalizationConfig) -> str:
    try:
        textpage = page.get_textpage_ocr(language=cfg.ocr_languages, dpi=cfg.ocr_dpi, full=True)
        text = page.get_text("text", textpage=textpage)
        return _safe_text(text)
    except Exception:
        return ""


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").replace("\r", "\n")
