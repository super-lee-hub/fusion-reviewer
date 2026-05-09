"""Deterministic artifact I/O — no LLM/API calls, no deepreview dependency."""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID


# ---- atomic I/O --------------------------------------------------------------

def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# ---- path helpers ------------------------------------------------------------

def _safe_display_stem(value: str) -> str:
    token = str(value or "").strip()
    token = re.sub(r"\s+", " ", token, flags=re.UNICODE)
    token = re.sub(r"[“”\"'`]+", "", token)
    token = re.sub(r"[-_]{2,}", "-", token).strip(" -_.")
    if len(token) > 80:
        token = token[:80].rstrip(" -_.")
    return token or "未命名论文"


def _slugify_stem(value: str) -> str:
    token = str(value or "").strip()
    token = re.sub(r"\s+", " ", token, flags=re.UNICODE)
    token = re.sub(r"[“”\"'`]+", "", token)
    token = re.sub(r"[-_]{2,}", "-", token).strip(" -_.")
    if len(token) > 80:
        token = token[:80].rstrip(" -_.")
    return token or "未命名论文"


def _copy_alias_file(source: Path, target: Path, *, written: dict[str, str], key: str) -> None:
    if not source.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        target.unlink()
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)
    written[key] = str(target)


def _paper_views_root(output_root: Path) -> Path:
    root = output_root / "按论文查看"
    root.mkdir(parents=True, exist_ok=True)
    return root


# ---- run directory -----------------------------------------------------------

def create_run_directory(
    output_root: Path,
    run_id: str,
    *,
    force: bool = False,
) -> Path:
    """Create a run directory. Returns the path. Fails if target exists and not forced."""
    run_dir = output_root / run_id
    if run_dir.exists():
        if force:
            shutil.rmtree(run_dir)
        else:
            raise FileExistsError(f"Run directory already exists: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def ensure_evidence_paths(run_dir: Path) -> dict[str, Path]:
    """Create evidence subdirectories and return path map."""
    evidence_dir = run_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    snapshots_dir = evidence_dir / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    original_dir = evidence_dir / "original"
    original_dir.mkdir(parents=True, exist_ok=True)

    reviews_dir = run_dir / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    return {
        "evidence_dir": evidence_dir,
        "snapshots_dir": snapshots_dir,
        "original_dir": original_dir,
        "reviews_dir": reviews_dir,
        "normalized_md": evidence_dir / "normalized.md",
        "plain_text": evidence_dir / "plain_text.txt",
        "page_index": evidence_dir / "page_index.json",
        "diagnostics": evidence_dir / "diagnostics.json",
        "structured_content": evidence_dir / "structured_content.json",
        "journal_requirements": evidence_dir / "journal_requirements.md",
        "manuscript_classification": evidence_dir / "manuscript_classification.json",
        "revision_notes": evidence_dir / "revision_notes.md",
        "previous_review": evidence_dir / "previous_review.md",
        "source_copy": evidence_dir / "source_copy",
        "normalized_pdf": evidence_dir / "normalized.pdf",
    }


# ---- structured artifact writers ---------------------------------------------

def write_review_json(path: Path, payload: dict[str, Any]) -> None:
    write_json_atomic(path, payload)


def write_review_markdown(path: Path, markdown: str) -> None:
    write_text_atomic(path, markdown)


def write_concerns_json(path: Path, concerns: list[dict[str, Any]]) -> None:
    write_json_atomic(path, concerns)


def write_concerns_csv(path: Path, concerns: list[dict[str, Any]]) -> None:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "id", "issue_key", "title", "description", "category",
                "severity", "raised_by", "specialist_flags",
                "needs_external_verification", "consensus_state",
            ],
        )
        writer.writeheader()
        for concern in concerns:
            writer.writerow({
                "id": concern.get("id", ""),
                "issue_key": concern.get("issue_key", ""),
                "title": concern.get("title", ""),
                "description": concern.get("description", ""),
                "category": concern.get("category", ""),
                "severity": concern.get("severity", ""),
                "raised_by": ";".join(concern.get("raised_by", [])),
                "specialist_flags": ";".join(concern.get("specialist_flags", [])),
                "needs_external_verification": concern.get("needs_external_verification", False),
                "consensus_state": concern.get("consensus_state", ""),
            })
    tmp.replace(path)


def write_final_report(path: Path, markdown: str) -> None:
    write_text_atomic(path, markdown)


def write_final_summary(path: Path, summary: dict[str, Any]) -> None:
    write_json_atomic(path, summary)


def write_meta_review(path: Path, editor: dict[str, Any]) -> None:
    write_json_atomic(path, editor)


def write_revision_assessment(path: Path, assessment: dict[str, Any]) -> None:
    write_json_atomic(path, assessment)


def write_revision_response_review(path: Path, review: dict[str, Any]) -> None:
    write_json_atomic(path, review)


# ---- friendly aliases --------------------------------------------------------

_FRIENDLY_ALIAS_MAP = {
    "committee_review_committee_reviewer_a": "11-委员会审稿-A",
    "committee_review_committee_reviewer_b": "12-委员会审稿-B",
    "committee_review_committee_reviewer_c": "13-委员会审稿-C",
    "specialist_review_theoretical": "21-理论专家审稿",
    "specialist_review_empirical": "22-方法专家审稿",
    "specialist_review_clarity": "23-表达专家审稿",
    "specialist_review_significance": "24-意义专家审稿",
    "specialist_review_structure": "25-结构专家审稿",
}


def _review_alias_stem(stem: str) -> str:
    return _FRIENDLY_ALIAS_MAP.get(stem, f"90-{_safe_display_stem(stem)}")


def sync_latest_results_view(
    run_dir: Path,
    *,
    title: str | None = None,
    source_name: str | None = None,
    output_root: Path | None = None,
) -> dict[str, str]:
    """Create human-friendly alias directory linked to the run directory.

    Uses hard links (fallback: copy) to avoid duplicating disk space.
    The output goes to ``<output_root>/按论文查看/<paper>/最新结果/``.
    """
    if output_root is None:
        output_root = run_dir.parent

    paper_display = _safe_display_stem(
        title or Path(source_name or "paper").stem
    )
    paper_dir = _paper_views_root(output_root) / paper_display
    paper_dir.mkdir(parents=True, exist_ok=True)
    latest_dir = paper_dir / "最新结果"
    if latest_dir.exists():
        shutil.rmtree(latest_dir)
    latest_dir.mkdir(parents=True, exist_ok=True)

    written: dict[str, str] = {
        "paper_results_dir": str(paper_dir),
        "latest_results_dir": str(latest_dir),
    }

    # 00-结果说明.txt
    guide_content = "\n".join([
        f"论文：{paper_display}",
        "",
        "怎么找文件：",
        "- 先看 01-审稿总报告.md 或 02-审稿总报告.pdf",
        "- 03-元审稿.md 是 editor / meta review",
        "- 04-问题汇总.csv 是去重后的问题汇总表",
        "- 10-Reviewer逐份意见 里是 8 位 reviewer 的逐份意见",
        "",
        f"本次真实运行目录：{run_dir}",
        f"按论文查看的最新结果目录：{latest_dir}",
        "",
        "- 06-返修回应审稿.md 是返修回应审核结果（仅返修稿件有此文件）",
        "",
        "说明：",
        "- reviews/ 是机器读写目录，文件名更偏程序化。",
        "- 10-Reviewer逐份意见 是给人直接查看的整理版目录。",
        "- 返修稿件中，诊断文件别名为07-提取诊断.json（非06），期刊要求为08-期刊要求.md（非07）。",
    ]).strip() + "\n"
    guide_path = latest_dir / "00-结果说明.txt"
    write_text_atomic(guide_path, guide_content)
    written["latest/00-结果说明.txt"] = str(guide_path)

    # Core aliases
    core_aliases = {
        "final_report.md": "01-审稿总报告.md",
        "final_report.pdf": "02-审稿总报告.pdf",
        "meta_review.md": "03-元审稿.md",
        "concerns_table.csv": "04-问题汇总.csv",
        "final_summary.json": "05-运行摘要.json",
        "evidence/diagnostics.json": "07-提取诊断.json",
        "evidence/journal_requirements.md": "08-期刊要求.md",
    }
    for relative_name, alias_name in core_aliases.items():
        _copy_alias_file(
            run_dir / relative_name,
            latest_dir / alias_name,
            written=written,
            key=f"latest/{alias_name}",
        )

    # Conditional: 06-返修回应审稿.md
    revision_path = run_dir / "revision_response_review.md"
    if revision_path.exists():
        _copy_alias_file(
            revision_path,
            latest_dir / "06-返修回应审稿.md",
            written=written,
            key="latest/06-返修回应审稿.md",
        )

    # Reviewer aliases
    reviews_source_dir = run_dir / "reviews"
    if reviews_source_dir.exists():
        alias_dir = latest_dir / "10-Reviewer逐份意见"
        if alias_dir.exists():
            shutil.rmtree(alias_dir)
        alias_dir.mkdir(parents=True, exist_ok=True)
        for source in sorted(reviews_source_dir.glob("*")):
            if source.is_dir():
                continue
            alias_stem = _review_alias_stem(source.stem)
            alias_path = alias_dir / f"{alias_stem}{source.suffix.lower()}"
            _copy_alias_file(
                source,
                alias_path,
                written=written,
                key=f"latest/10-Reviewer逐份意见/{alias_path.name}",
            )

    return written
