from __future__ import annotations

import csv
import importlib
import json
import shutil
from pathlib import Path
from uuid import uuid4

from .config import get_settings
from .document_io import detect_libreoffice_binary, extract_docx_text
from .evidence import export_pdf_report
from .models import AgentReview, EditorReport
from .normalization import NormalizationConfig, normalize_document
from .orchestration import (
    build_final_report,
    merge_concerns,
    render_agent_markdown,
    render_editor_markdown,
    summarize_review_sources,
    with_inferred_review_source,
)
from .storage import build_run_label, write_friendly_aliases_for_run_dir
from .text_utils import decode_text_file, looks_garbled


EXPECTED_CODEX_SUBAGENT_REVIEWS = 8
DEFAULT_CODEX_ENV_NAME = "review-fusion-py313"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _decode_journal_file(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        return extract_docx_text(path).strip()
    return decode_text_file(path).strip()


def _build_journal_markdown(
    *,
    journal_text: str | None = None,
    journal_file_path: str | Path | None = None,
) -> tuple[str | None, str | None]:
    sections: list[str] = []
    source_parts: list[str] = []
    if journal_text and journal_text.strip():
        sections.extend(["# 期刊要求", "", journal_text.strip()])
        source_parts.append("text")
    if journal_file_path:
        path = Path(journal_file_path).resolve()
        if path.exists():
            decoded = _decode_journal_file(path)
            if decoded:
                if sections:
                    sections.extend(["", "---", ""])
                sections.extend([f"# 期刊要求文件：{path.name}", "", decoded])
                source_parts.append(f"file:{path.name}")
    if not sections:
        return None, None
    return "\n".join(sections).strip() + "\n", ", ".join(source_parts)


def _module_importable(name: str) -> bool:
    try:
        importlib.import_module(name)
    except Exception:
        return False
    return True


def _environment_status() -> dict[str, object]:
    settings = get_settings()
    libreoffice_path = detect_libreoffice_binary(settings.libreoffice_bin)
    return {
        "python_executable": importlib.import_module("sys").executable,
        "fusion_reviewer_importable": _module_importable("fusion_reviewer"),
        "deepreview_importable": _module_importable("deepreview"),
        "mineru_token_present": bool(settings.mineru_api_token),
        "mineru_base_url": settings.mineru_base_url,
        "libreoffice_available": bool(libreoffice_path),
        "libreoffice_path": libreoffice_path,
    }


def _powershell_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _recommended_finalize_commands(run_dir: Path) -> dict[str, str]:
    quoted_run_dir = _powershell_quote(run_dir)
    return {
        "primary_cli": (
            f"conda run -n {DEFAULT_CODEX_ENV_NAME} "
            f"fusion-review codex finalize-from-reviews --run-dir {quoted_run_dir}"
        ),
        "fallback_script": (
            f"conda run -n {DEFAULT_CODEX_ENV_NAME} "
            f"python .\\codex-skill\\scripts\\finalize_run.py --run-dir {quoted_run_dir}"
        ),
    }


def _find_shell_corruption(payload: object, *, path: str) -> str | None:
    if isinstance(payload, str):
        if looks_garbled(payload):
            return path
        return None
    if isinstance(payload, dict):
        for key, value in payload.items():
            hit = _find_shell_corruption(value, path=f"{path}.{key}")
            if hit:
                return hit
        return None
    if isinstance(payload, list):
        for idx, value in enumerate(payload):
            hit = _find_shell_corruption(value, path=f"{path}[{idx}]")
            if hit:
                return hit
        return None
    return None


def _validate_finalize_payload(*, title: str, source_name: str, reviews: list[dict], editor: dict) -> None:
    checks: list[tuple[str, object]] = [
        ("title", title),
        ("source_name", source_name),
        ("editor", editor),
        ("reviews", reviews),
    ]
    for root, payload in checks:
        hit = _find_shell_corruption(payload, path=root)
        if hit:
            raise ValueError(
                "Detected shell-corrupted finalize payload at "
                f"{hit}. This usually means Chinese text was piped through PowerShell "
                "into `python -` and got replaced with `?`. Re-run finalization with "
                "`fusion-review codex finalize-from-reviews --run-dir <run_dir>` or "
                "save the temporary script as a UTF-8 .py file instead of piping inline code."
            )


def _build_normalization_config(output_root: Path) -> NormalizationConfig:
    settings = get_settings()
    return NormalizationConfig(
        output_root=output_root / "_normalize_cache",
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


def prepare_codex_run(
    paper_path: str | Path,
    *,
    output_root: str | Path | None = None,
    run_id: str | None = None,
    journal_text: str | None = None,
    journal_file_path: str | Path | None = None,
    revision_text: str | None = None,
    revision_file: str | Path | None = None,
    previous_review_dir: str | Path | None = None,
    previous_review_file: str | Path | None = None,
) -> dict[str, object]:
    source = Path(paper_path).resolve()
    settings = get_settings()
    root = Path(output_root).resolve() if output_root else settings.data_dir
    run_token = run_id or str(uuid4())
    paper_stem = source.stem
    run_label = build_run_label(paper_stem, run_token)
    run_dir = root / run_label
    evidence_dir = run_dir / "evidence"
    reviews_dir = run_dir / "reviews"
    snapshots_dir = evidence_dir / "snapshots"
    finalize_commands = _recommended_finalize_commands(run_dir)
    reviews_dir.mkdir(parents=True, exist_ok=True)
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    _write_text(
        run_dir / "00-当前仅完成预处理.txt",
        "\n".join(
            [
                "这次 run 目前只完成了预处理。",
                "",
                "如果你现在看到 reviews/ 是空的，通常不是文件丢了，而是还没有进入 finalize 阶段。",
                "只有在 8 个 reviewer 结果和 editor 收口都完成后，系统才会把 reviewer 文档、元审稿和最终报告写进来。",
                "",
                "当 reviews/ 里已经有 reviewer JSON 后，请直接运行正式收口命令，不要手写 tmp_finalize_*.py 之类的一次性脚本：",
                f"- 首选（CLI）：{finalize_commands['primary_cli']}",
                f"- 备选（脚本）：{finalize_commands['fallback_script']}",
                "- 上面两条命令都会自动从 run 目录解析 title/source-name；不需要再手填一次。",
                "",
                "完成后请优先查看：",
                "- 01-审稿总报告.md",
                "- 02-审稿总报告.pdf",
                "- 03-元审稿.md",
                "- 10-Reviewer逐份意见/",
            ]
        ).strip()
        + "\n",
    )

    source_copy = evidence_dir / f"source_copy{source.suffix.lower()}"
    shutil.copy2(source, source_copy)
    journal_markdown, journal_source = _build_journal_markdown(
        journal_text=journal_text,
        journal_file_path=journal_file_path,
    )
    if journal_markdown:
        _write_text(evidence_dir / "journal_requirements.md", journal_markdown)

    # --- revision materials ---
    revision_markdown: str | None = None
    revision_source: str | None = None
    revision_quality: str | None = None
    if revision_text and revision_text.strip():
        revision_markdown = revision_text.strip() + "\n"
        revision_source = "text"
    if revision_file:
        rev_path = Path(revision_file).resolve()
        if rev_path.exists():
            if rev_path.suffix.lower() == ".docx":
                revision_markdown = extract_docx_text(rev_path).strip() + "\n"
            else:
                revision_markdown = decode_text_file(rev_path).strip() + "\n"
            if revision_source:
                revision_source += f", file:{rev_path.name}"
            else:
                revision_source = f"file:{rev_path.name}"
    if revision_markdown:
        revision_quality = "garbled" if looks_garbled(revision_markdown) else "good"
        _write_text(evidence_dir / "revision_notes.md", revision_markdown)

    if previous_review_file:
        prev_path = Path(previous_review_file).resolve()
        if prev_path.exists():
            shutil.copy2(prev_path, evidence_dir / "previous_review.md")
    elif previous_review_dir:
        prev_dir = Path(previous_review_dir).resolve()
        if prev_dir.exists():
            md_files = sorted(prev_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
            if md_files:
                shutil.copy2(md_files[0], evidence_dir / "previous_review.md")

    normalized = normalize_document(source_copy, config=_build_normalization_config(root))
    shutil.copy2(normalized.artifacts.markdown_path, evidence_dir / "normalized.md")
    shutil.copy2(normalized.artifacts.plain_text_path, evidence_dir / "plain_text.txt")
    shutil.copy2(normalized.artifacts.page_index_path, evidence_dir / "page_index.json")
    shutil.copy2(normalized.artifacts.structured_json_path, evidence_dir / "structured.json")

    diagnostics = {**normalized.diagnostics, "cache_hit": normalized.cache_hit, "cache_key": normalized.cache_key}
    diagnostics["environment_status"] = _environment_status()
    diagnostics["journal_context_present"] = bool(journal_markdown)
    diagnostics["journal_context_source"] = journal_source
    diagnostics["revision_context_present"] = bool(revision_markdown)
    diagnostics["revision_context_source"] = revision_source
    diagnostics["revision_extraction_quality"] = revision_quality

    copied_snapshots: list[str] = []
    for snapshot in normalized.snapshot_paths:
        if not snapshot.exists():
            continue
        target = snapshots_dir / snapshot.name
        shutil.copy2(snapshot, target)
        copied_snapshots.append(str(target))
    diagnostics["run_snapshot_paths"] = copied_snapshots
    _write_json(evidence_dir / "diagnostics.json", diagnostics)

    normalized_pdf = evidence_dir / "normalized.pdf"
    if normalized.normalized_source_path.exists() and normalized.normalized_source_path.suffix.lower() == ".pdf":
        shutil.copy2(normalized.normalized_source_path, normalized_pdf)

    manifest = {
        "paper_path": str(source),
        "paper_stem": paper_stem,
        "run_id": run_token,
        "run_label": run_label,
        "output_root": str(root),
        "run_dir": str(run_dir),
        "document_type": normalized.document_kind,
        "layout_fidelity": normalized.layout_fidelity,
        "extractor_used": normalized.extractor_used,
        "conversion_used": normalized.conversion_used,
        "normalized_source_path": str(normalized.normalized_source_path),
        "evidence_dir": str(evidence_dir),
        "reviews_dir": str(reviews_dir),
        "mode": "codex",
        "journal_context_present": bool(journal_markdown),
        "journal_context_source": journal_source,
        "revision_context_present": bool(revision_markdown),
        "revision_context_source": revision_source,
        "revision_extraction_quality": revision_quality,
        "environment_status": diagnostics["environment_status"],
        "prepare_only_note": str(run_dir / "00-当前仅完成预处理.txt"),
        "recommended_finalize_command": finalize_commands["primary_cli"],
        "recommended_finalize_fallback_command": finalize_commands["fallback_script"],
    }
    _write_json(evidence_dir / "prepare_manifest.json", manifest)
    return manifest


def _review_stem(review: AgentReview) -> str:
    if review.kind == "generalist":
        return f"committee_review_{review.agent_id}"
    if review.kind == "specialist":
        if review.agent_id.startswith("specialist_"):
            return f"specialist_review_{review.agent_id.removeprefix('specialist_')}"
        category = review.findings[0].category if review.findings else review.agent_id
        return f"specialist_review_{category}"
    return "meta_review"


def _purge_stale_review_files(reviews_path: Path, review_models: list[AgentReview]) -> None:
    expected_stems = {review.agent_id: _review_stem(review) for review in review_models}
    for path in reviews_path.glob("*.json"):
        try:
            existing = AgentReview.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        expected_stem = expected_stems.get(existing.agent_id)
        if not expected_stem or path.stem == expected_stem:
            continue
        path.unlink(missing_ok=True)
        markdown_path = path.with_suffix(".md")
        markdown_path.unlink(missing_ok=True)


def finalize_codex_run(
    run_dir: str | Path,
    *,
    title: str,
    source_name: str,
    reviews: list[dict],
    editor: dict,
    provider_profile: str | None = None,
) -> dict[str, object]:
    run_path = Path(run_dir)
    reviews_path = run_path / "reviews"
    evidence_dir = run_path / "evidence"
    reviews_path.mkdir(parents=True, exist_ok=True)
    _validate_finalize_payload(title=title, source_name=source_name, reviews=reviews, editor=editor)

    review_models = [with_inferred_review_source(AgentReview.model_validate(item)) for item in reviews]
    _purge_stale_review_files(reviews_path, review_models)
    source_audit = summarize_review_sources(review_models, expected_subagent_reviews=EXPECTED_CODEX_SUBAGENT_REVIEWS)
    for review in review_models:
        stem = _review_stem(review)
        _write_text(reviews_path / f"{stem}.md", render_agent_markdown(review))
        _write_json(reviews_path / f"{stem}.json", review.model_dump(mode="json"))

    completed = [item for item in review_models if item.status == "completed"]
    concerns = merge_concerns(completed)
    _write_json(run_path / "concerns_table.json", [item.model_dump(mode="json") for item in concerns])
    with (run_path / "concerns_table.csv").open("w", encoding="utf-8", newline="") as handle:
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

    editor_model = EditorReport.model_validate(
        {
            **editor,
            **source_audit,
        }
    )
    _write_text(run_path / "meta_review.md", render_editor_markdown(editor_model))
    _write_json(run_path / "meta_review.json", editor_model.model_dump(mode="json"))

    journal_requirements = None
    journal_path = evidence_dir / "journal_requirements.md"
    if journal_path.exists():
        journal_requirements = journal_path.read_text(encoding="utf-8")

    diagnostics = {}
    diagnostics_path = evidence_dir / "diagnostics.json"
    if diagnostics_path.exists():
        diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))

    # --- revision response review (before build_final_report, so it appears in main report) ---
    revision_review_result = None
    revision_review_usage = None
    revision_context_present = diagnostics.get("revision_context_present", False)
    if revision_context_present and provider_profile:
        revision_notes_path = evidence_dir / "revision_notes.md"
        previous_review_path = evidence_dir / "previous_review.md"
        if revision_notes_path.exists():
            try:
                from .providers import ProviderRegistry
                from .revision_review import review_revision_response
                registry = ProviderRegistry()
                revision_text = revision_notes_path.read_text(encoding="utf-8")
                previous_text = previous_review_path.read_text(encoding="utf-8") if previous_review_path.exists() else None
                revision_review_result = review_revision_response(
                    provider=registry.build(provider_profile),
                    revision_text=revision_text,
                    concerns=concerns,
                    previous_review_markdown=previous_text,
                    title=title,
                )
                _write_text(run_path / "revision_response_review.md", revision_review_result.markdown)
                _write_json(run_path / "revision_response_review.json", revision_review_result.json_payload)
                revision_review_usage = revision_review_result.usage
            except Exception:
                revision_review_result = None
    elif revision_context_present and not provider_profile:
        _write_text(run_path / "revision_response_review.md",
            "## 返修回应审稿\n\n"
            "⚠️ 检测到返修上下文，但 codex 收口阶段未提供 provider_profile，无法运行返修回应审稿。\n"
            "请使用 backend 模式或提供 API key 后重新收口。\n")

    final_markdown = build_final_report(
        title,
        completed,
        concerns,
        editor_model,
        journal_requirements=journal_requirements,
        journal_context_source=diagnostics.get("journal_context_source"),
        layout_fidelity=diagnostics.get("layout_fidelity"),
        expected_subagent_reviews=EXPECTED_CODEX_SUBAGENT_REVIEWS,
        revision_review_result=revision_review_result,
        revision_context_present=revision_context_present,
    )
    _write_text(run_path / "final_report.md", final_markdown)

    final_summary = {
        "title": title,
        "source_name": source_name,
        "decision": editor_model.decision,
        "concerns_count": len(concerns),
        "completed_reviews": len(completed),
        "failed_reviews": sum(item.status != "completed" for item in review_models),
        "journal_context_present": bool(journal_requirements),
        "journal_context_source": diagnostics.get("journal_context_source"),
        "extractor_used": diagnostics.get("extractor_used"),
        "layout_fidelity": diagnostics.get("layout_fidelity"),
        "conversion_used": diagnostics.get("conversion_used"),
        "mineru_attempted": diagnostics.get("mineru_attempted"),
        "mineru_succeeded": diagnostics.get("mineru_succeeded"),
        "revision_context_present": revision_context_present,
        "revision_context_source": diagnostics.get("revision_context_source"),
        "revision_extraction_quality": diagnostics.get("revision_extraction_quality"),
        **source_audit,
    }
    _write_json(run_path / "final_summary.json", final_summary)

    structured_path = evidence_dir / "structured.json"
    content_list = None
    if structured_path.exists():
        payload = json.loads(structured_path.read_text(encoding="utf-8"))
        content_list = payload.get("content_list")

    source_pdf = evidence_dir / "normalized.pdf"
    if not source_pdf.exists():
        source_candidates = sorted(evidence_dir.glob("source_copy.pdf"))
        source_pdf = source_candidates[0] if source_candidates else source_pdf
    if source_pdf.exists():
        export_pdf_report(
            settings=get_settings(),
            job_id=run_path.name,
            title=title,
            source_name=source_name,
            source_pdf_path=source_pdf,
            final_markdown=final_markdown,
            content_list=content_list,
            annotations=[],
            token_usage={"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            agent_model=editor_model.model,
            report_pdf_path=run_path / "final_report.pdf",
        )
    alias_files = write_friendly_aliases_for_run_dir(
        run_path,
        title=title,
        source_name=source_name,
        run_token=run_path.name,
    )

    return {
        "run_dir": str(run_path),
        "latest_results_dir": alias_files.get("latest_results_dir"),
        "paper_results_dir": alias_files.get("paper_results_dir"),
        "review_source_audit": source_audit,
        "concerns": [item.model_dump(mode="json") for item in concerns],
        "editor": editor_model.model_dump(mode="json"),
        "final_summary": final_summary,
        "revision_review_result": revision_review_result.json_payload if revision_review_result else None,
        "revision_review_usage": revision_review_usage,
        "revision_context_present": revision_context_present,
        "friendly_artifact_aliases": alias_files,
    }
