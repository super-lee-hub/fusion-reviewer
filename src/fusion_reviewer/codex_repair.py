from __future__ import annotations

import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from .codex_runtime import finalize_codex_run
from .document_io import build_markdown_from_page_index, extract_docx_text
from .text_utils import looks_garbled
from .models import AgentReview, EditorReport
from .normalization import NormalizationConfig, _assess_docx_pdf_alignment
from .orchestration import merge_concerns


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _timestamp_token() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _resolve_run_metadata(
    run_dir: Path,
    *,
    title: str | None,
    source_name: str | None,
) -> tuple[str, str]:
    final_summary_path = run_dir / "final_summary.json"
    prepare_manifest_path = run_dir / "evidence" / "prepare_manifest.json"

    resolved_title = title
    resolved_source_name = source_name

    if final_summary_path.exists():
        payload = _read_json(final_summary_path)
        resolved_title = resolved_title or str(payload.get("title") or "").strip() or None
        resolved_source_name = resolved_source_name or str(payload.get("source_name") or "").strip() or None

    if prepare_manifest_path.exists():
        payload = _read_json(prepare_manifest_path)
        resolved_title = resolved_title or str(payload.get("paper_stem") or "").strip() or None

    source_copy = next(iter(sorted((run_dir / "evidence").glob("source_copy.*"))), None)
    if source_copy is not None:
        resolved_source_name = resolved_source_name or source_copy.name
        if not resolved_title:
            resolved_title = source_copy.stem

    resolved_title = resolved_title or run_dir.name
    resolved_source_name = resolved_source_name or resolved_title
    return resolved_title, resolved_source_name


def _backup_run_artifacts(run_dir: Path) -> Path:
    backup_dir = run_dir / "_repair_backups" / _timestamp_token()
    backup_targets = [
        "reviews_input.json",
        "editor_input.json",
        "meta_review.md",
        "meta_review.json",
        "final_report.md",
        "final_report.pdf",
        "concerns_table.csv",
        "concerns_table.json",
        "final_summary.json",
        "01-审稿总报告.md",
        "02-审稿总报告.pdf",
        "03-元审稿.md",
        "04-问题汇总.csv",
        "05-运行摘要.json",
        "91-reviewer输入汇总.json",
        "92-editor输入汇总.json",
        "evidence/normalized.md",
        "evidence/plain_text.txt",
        "evidence/page_index.json",
        "evidence/paragraph_index.json",
        "evidence/review_evidence_notes.json",
        "evidence/diagnostics.json",
    ]
    for relative_name in backup_targets:
        source = run_dir / relative_name
        if not source.exists():
            continue
        target = backup_dir / relative_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    # Keep only the latest 2 backups to prevent unbounded growth.
    backups_parent = run_dir / "_repair_backups"
    existing = sorted(
        [d for d in backups_parent.iterdir() if d.is_dir()],
        key=lambda p: p.name,
        reverse=True,
    )
    for stale in existing[2:]:
        shutil.rmtree(stale)

    return backup_dir


def _build_paragraph_index(docx_text: str) -> list[dict[str, Any]]:
    paragraphs = [line.strip() for line in docx_text.splitlines() if line.strip()]
    return [
        {
            "paragraph": idx,
            "locator": f"docx para {idx}",
            "text": text,
        }
        for idx, text in enumerate(paragraphs, start=1)
    ]


def _repair_docx_evidence_if_needed(run_dir: Path, *, force: bool = False) -> dict[str, Any]:
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

    _write_text(evidence_dir / "normalized.md", normalized_markdown)
    _write_text(evidence_dir / "plain_text.txt", "\n".join(page_lines).strip() + "\n")
    _write_json(evidence_dir / "page_index.json", {1: page_lines})
    _write_json(evidence_dir / "paragraph_index.json", paragraph_index)
    _write_json(
        evidence_dir / "review_evidence_notes.json",
        {
            "authoritative_evidence": "normalized.md",
            "authoritative_evidence_source": "docx-native",
            "evidence_locator_scheme": "Use locators such as docx para 37.",
            "reason": "repair-run replaced garbled PDF-derived evidence with DOCX-native text.",
            "quality_check": assessment,
        },
    )

    diagnostics.update(
        {
            "repair_run_applied": True,
            "repair_authoritative_evidence_source": "docx-native",
            "repair_quality_check": assessment,
            "layout_fidelity": "degraded",
            "extractor_used": "docx-text",
            "conversion_used": diagnostics.get("conversion_used") or "libreoffice->pdf+docx-text-authoritative",
        }
    )
    _write_json(diagnostics_path, diagnostics)
    return {
        "applied": True,
        "reason": "docx_native_evidence_restored",
        "paragraph_count": len(paragraph_index),
        "quality_check": assessment,
    }




def _load_reviews_from_individual_files(run_dir: Path) -> list[AgentReview]:
    return _load_reviews_from_directory(run_dir / "reviews")


def _load_reviews_from_directory(reviews_dir: Path) -> list[AgentReview]:
    reviews_by_agent: dict[str, tuple[AgentReview, Path]] = {}
    for path in sorted(reviews_dir.glob("*.json")):
        if path.name == "meta_review.json":
            continue
        try:
            raw = path.read_text(encoding="utf-8")
            if looks_garbled(raw):
                continue
            review = AgentReview.model_validate(json.loads(raw))
        except Exception:
            continue
        incumbent = reviews_by_agent.get(review.agent_id)
        if incumbent is None:
            reviews_by_agent[review.agent_id] = (review, path)
            continue
        _, incumbent_path = incumbent
        preferred = _prefer_review_path(review, path, incumbent_path)
        if preferred == path:
            reviews_by_agent[review.agent_id] = (review, path)
    return [item[0] for item in reviews_by_agent.values()]


def _prefer_review_path(review: AgentReview, candidate_path: Path, incumbent_path: Path) -> Path:
    canonical_stem = _canonical_review_stem(review)
    candidate_is_canonical = candidate_path.stem == canonical_stem
    incumbent_is_canonical = incumbent_path.stem == canonical_stem
    if candidate_is_canonical and not incumbent_is_canonical:
        return candidate_path
    if incumbent_is_canonical and not candidate_is_canonical:
        return incumbent_path
    if candidate_path.stat().st_mtime >= incumbent_path.stat().st_mtime:
        return candidate_path
    return incumbent_path


def _canonical_review_stem(review: AgentReview) -> str:
    if review.kind == "generalist":
        return f"committee_review_{review.agent_id}"
    if review.kind == "specialist" and review.agent_id.startswith("specialist_"):
        return f"specialist_review_{review.agent_id.removeprefix('specialist_')}"
    if review.kind == "specialist":
        category = review.findings[0].category if review.findings else review.agent_id
        return f"specialist_review_{category}"
    return "meta_review"


def _load_reviews_from_combined_file(run_dir: Path) -> list[AgentReview]:
    path = run_dir / "reviews_input.json"
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8", errors="ignore")
    if looks_garbled(raw):
        return []
    try:
        payload = json.loads(raw)
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    reviews: list[AgentReview] = []
    for item in payload:
        try:
            reviews.append(AgentReview.model_validate(item))
        except Exception:
            continue
    return reviews


def _load_reviews_from_payload_file(path: Path) -> list[AgentReview]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    if looks_garbled(raw):
        raise RuntimeError(f"Reviewer payload appears shell-corrupted: {path}")
    payload = json.loads(raw)
    if not isinstance(payload, list):
        raise RuntimeError(f"Reviewer payload must be a JSON list: {path}")
    reviews: list[AgentReview] = []
    for item in payload:
        reviews.append(AgentReview.model_validate(item))
    if not reviews:
        raise RuntimeError(f"Reviewer payload file is empty: {path}")
    return reviews


def _load_recoverable_reviews(run_dir: Path) -> list[AgentReview]:
    reviews = _load_reviews_from_individual_files(run_dir)
    if reviews:
        return reviews
    reviews = _load_reviews_from_combined_file(run_dir)
    if reviews:
        return reviews
    raise RuntimeError(
        "找不到可恢复的 reviewer JSON。repair-run 可以重建汇总文件，但前提是 run 目录里至少还保留一份未损坏的 reviewer JSON。"
    )


def _load_editor_from_file(path: Path) -> EditorReport:
    raw = path.read_text(encoding="utf-8")
    if looks_garbled(raw):
        raise RuntimeError(f"Editor payload appears shell-corrupted: {path}")
    return EditorReport.model_validate(json.loads(raw))


def _load_existing_editor(run_dir: Path) -> EditorReport | None:
    path = run_dir / "editor_input.json"
    if not path.exists():
        return None
    try:
        return _load_editor_from_file(path)
    except Exception:
        return None


def _pick_editor_decision(reviews: list[AgentReview], concern_count: int, max_severity: str) -> str:
    counts = Counter(review.recommendation for review in reviews if review.status == "completed")
    completed = sum(counts.values())
    if completed == 0:
        return "major_revision"
    if counts.get("reject", 0) >= max(2, completed // 2 + 1):
        return "reject"
    if max_severity == "critical" or counts.get("major_revision", 0) + counts.get("reject", 0) >= max(1, completed // 2):
        return "major_revision"
    if concern_count >= 2 or counts.get("minor_revision", 0) > 0:
        return "minor_revision"
    return "accept"


def _build_editor_report_from_reviews(
    reviews: list[AgentReview],
    *,
    evidence_repair_note: dict[str, Any],
) -> EditorReport:
    completed = [item for item in reviews if item.status == "completed"]
    concerns = merge_concerns(completed)
    counts = Counter(review.recommendation for review in completed)
    max_severity = concerns[0].severity if concerns else "medium"
    decision = _pick_editor_decision(completed, len(concerns), max_severity)

    consensus = [
        f"多位 reviewer 共同指出：{concern.title}。"
        for concern in concerns
        if concern.consensus_state == "consensus"
    ][:5]
    if not consensus and concerns:
        consensus = [f"当前最突出的单一问题是：{concerns[0].title}。"]

    recommendation_set = sorted({review.recommendation for review in completed})
    disagreements: list[str] = []
    if len(recommendation_set) > 1:
        disagreements.append(
            "reviewer 的建议等级并不完全一致："
            + "、".join(f"{key}={value}" for key, value in sorted(counts.items()))
            + "。"
        )
    single_source = [concern.title for concern in concerns if concern.consensus_state == "single-source"][:4]
    if single_source:
        disagreements.append("以下问题目前主要由个别 reviewer 提出：" + "；".join(single_source) + "。")

    priority_revisions = [
        f"优先修复：{concern.title}（{concern.category}/{concern.severity}）。"
        for concern in concerns[:5]
    ] or ["优先核对 reviewer 原始意见，并补齐最严重的问题对应证据。"]

    rationale = (
        f"本次 repair-run 基于 {len(completed)} 份可恢复 reviewer JSON 重新汇总。"
        f" 合并后得到 {len(concerns)} 个问题簇，最高严重度为 {max_severity}，因此给出 `{decision}`。"
    )
    if evidence_repair_note.get("applied"):
        rationale += " 同时检测到旧 evidence 可能受 PDF 抽取乱码影响，已切换为 DOCX 原生文本作为修复后的权威证据。"

    return EditorReport.model_validate(
        {
            "agent_id": "meta_editor_repair",
            "title": "本地修复汇总编辑",
            "provider_profile": "local_repair",
            "model": "deterministic-repair-v1",
            "decision": decision,
            "consensus": consensus,
            "disagreements": disagreements,
            "priority_revisions": priority_revisions,
            "decision_rationale": rationale,
            "status": "completed",
        }
    )


def _resolve_editor_report(
    run_dir: Path,
    *,
    editor_file: str | Path | None,
    reviews: list[AgentReview],
    evidence_repair_note: dict[str, Any],
) -> tuple[EditorReport, str]:
    if editor_file:
        return _load_editor_from_file(Path(editor_file).resolve()), "provided_editor_file"

    existing_editor = _load_existing_editor(run_dir)
    if existing_editor is not None:
        return existing_editor, "existing_editor_input"

    return _build_editor_report_from_reviews(reviews, evidence_repair_note=evidence_repair_note), "generated_from_reviews"


def rebuild_codex_run_from_reviews(
    run_dir: str | Path,
    *,
    title: str | None = None,
    source_name: str | None = None,
    reviews_dir: str | Path | None = None,
    editor_file: str | Path | None = None,
    force_docx_evidence: bool = False,
    provider_profile: str | None = None,
    summary_filename: str = "rebuild_summary.json",
) -> dict[str, Any]:
    run_path = Path(run_dir).resolve()
    if not run_path.exists():
        raise FileNotFoundError(f"Run directory not found: {run_path}")

    backup_dir = _backup_run_artifacts(run_path)
    evidence_repair = _repair_docx_evidence_if_needed(run_path, force=force_docx_evidence)

    if reviews_dir:
        resolved_reviews_dir = Path(reviews_dir).resolve()
        if not resolved_reviews_dir.exists():
            raise FileNotFoundError(f"Reviews directory not found: {resolved_reviews_dir}")
        reviews = _load_reviews_from_directory(resolved_reviews_dir)
        if not reviews:
            raise RuntimeError(f"指定的 reviews 目录里没有可恢复的 reviewer JSON：{resolved_reviews_dir}")
    else:
        resolved_reviews_dir = run_path / "reviews"
        reviews = _load_recoverable_reviews(run_path)

    resolved_title, resolved_source_name = _resolve_run_metadata(
        run_path,
        title=title,
        source_name=source_name,
    )
    editor, editor_source = _resolve_editor_report(
        run_path,
        editor_file=editor_file,
        reviews=reviews,
        evidence_repair_note=evidence_repair,
    )

    _write_json(run_path / "reviews_input.json", [item.model_dump(mode="json") for item in reviews])
    _write_json(run_path / "editor_input.json", editor.model_dump(mode="json"))
    result = finalize_codex_run(
        run_path,
        title=resolved_title,
        source_name=resolved_source_name,
        reviews=[item.model_dump(mode="json") for item in reviews],
        editor=editor.model_dump(mode="json"),
        provider_profile=provider_profile,
    )

    rebuild_summary = {
        "run_dir": str(run_path),
        "backup_dir": str(backup_dir),
        "title": resolved_title,
        "source_name": resolved_source_name,
        "reviews_dir": str(resolved_reviews_dir),
        "review_count": len(reviews),
        "completed_review_count": sum(item.status == "completed" for item in reviews),
        "editor_source": editor_source,
        "evidence_repair": evidence_repair,
        "decision": result["final_summary"]["decision"],
        "latest_results_dir": result.get("latest_results_dir"),
    }
    _write_json(run_path / summary_filename, rebuild_summary)
    return {
        **result,
        "backup_dir": str(backup_dir),
        "rebuild_summary": rebuild_summary,
    }


def finalize_codex_run_from_payload_files(
    run_dir: str | Path,
    *,
    reviews_file: str | Path,
    editor_file: str | Path,
    title: str | None = None,
    source_name: str | None = None,
    provider_profile: str | None = None,
    summary_filename: str = "payload_finalize_summary.json",
) -> dict[str, Any]:
    run_path = Path(run_dir).resolve()
    if not run_path.exists():
        raise FileNotFoundError(f"Run directory not found: {run_path}")

    reviews_path = Path(reviews_file).resolve()
    editor_path = Path(editor_file).resolve()
    if not reviews_path.exists():
        raise FileNotFoundError(f"Reviews payload file not found: {reviews_path}")
    if not editor_path.exists():
        raise FileNotFoundError(f"Editor payload file not found: {editor_path}")

    backup_dir = _backup_run_artifacts(run_path)
    reviews = _load_reviews_from_payload_file(reviews_path)
    editor = _load_editor_from_file(editor_path)
    resolved_title, resolved_source_name = _resolve_run_metadata(
        run_path,
        title=title,
        source_name=source_name,
    )

    _write_json(run_path / "reviews_input.json", [item.model_dump(mode="json") for item in reviews])
    _write_json(run_path / "editor_input.json", editor.model_dump(mode="json"))
    result = finalize_codex_run(
        run_path,
        title=resolved_title,
        source_name=resolved_source_name,
        reviews=[item.model_dump(mode="json") for item in reviews],
        editor=editor.model_dump(mode="json"),
        provider_profile=provider_profile,
    )

    payload_summary = {
        "run_dir": str(run_path),
        "backup_dir": str(backup_dir),
        "title": resolved_title,
        "source_name": resolved_source_name,
        "reviews_file": str(reviews_path),
        "editor_file": str(editor_path),
        "review_count": len(reviews),
        "completed_review_count": sum(item.status == "completed" for item in reviews),
        "decision": result["final_summary"]["decision"],
        "latest_results_dir": result.get("latest_results_dir"),
    }
    _write_json(run_path / summary_filename, payload_summary)
    return {
        **result,
        "backup_dir": str(backup_dir),
        "payload_finalize_summary": payload_summary,
    }


def repair_codex_run(
    run_dir: str | Path,
    *,
    title: str | None = None,
    source_name: str | None = None,
    force_docx_evidence: bool = False,
) -> dict[str, Any]:
    payload = rebuild_codex_run_from_reviews(
        run_dir,
        title=title,
        source_name=source_name,
        force_docx_evidence=force_docx_evidence,
        summary_filename="repair_summary.json",
    )
    return {
        **payload,
        "repair_summary": payload["rebuild_summary"],
    }
