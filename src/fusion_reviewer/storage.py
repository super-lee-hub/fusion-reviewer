from __future__ import annotations

import json
import os
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from .config import get_settings
from .models import FusionJobState


_LOCK = threading.RLock()


def jobs_root() -> Path:
    root = get_settings().data_dir
    root.mkdir(parents=True, exist_ok=True)
    return root


def job_index_root() -> Path:
    root = jobs_root() / ".job_index"
    root.mkdir(parents=True, exist_ok=True)
    return root


def paper_views_root() -> Path:
    root = jobs_root() / "按论文查看"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_job_id(job_id: UUID | str) -> str:
    token = str(job_id)
    UUID(token)
    return token


def _slugify_stem(value: str) -> str:
    token = (value or "").strip().lower()
    token = re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", "-", token)
    token = re.sub(r"\s+", "-", token, flags=re.UNICODE)
    token = re.sub(r"[^\w\-\u4e00-\u9fff]+", "-", token, flags=re.UNICODE)
    token = re.sub(r"-{2,}", "-", token).strip("-_. ")
    if len(token) > 64:
        token = token[:64].rstrip("-_. ")
    return token or "paper"


def _safe_display_stem(value: str) -> str:
    token = (value or "").strip()
    token = re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", "-", token)
    token = token.replace("《", "").replace("》", "")
    token = re.sub(r"[\r\n\t]+", " ", token)
    token = re.sub(r"\s+", " ", token, flags=re.UNICODE)
    token = re.sub(r"[“”\"'`]+", "", token)
    token = re.sub(r"[-_]{2,}", "-", token).strip(" -_.")
    if len(token) > 80:
        token = token[:80].rstrip(" -_.")
    return token or "未命名论文"


def _looks_garbled_name(value: str | None) -> bool:
    token = (value or "").strip()
    if not token:
        return True
    if "\ufffd" in token:
        return True
    question_marks = token.count("?")
    return question_marks >= max(3, len(token) // 3)


def _run_label_display_stem(run_dir: Path | None) -> str | None:
    if run_dir is None:
        return None
    parts = run_dir.name.split("__")
    if len(parts) >= 3:
        candidate = parts[1].strip()
        if candidate and not _looks_garbled_name(candidate):
            return candidate
    return None


def build_run_label(paper_stem: str, run_token: UUID | str, *, created_at: datetime | None = None) -> str:
    timestamp = (created_at or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    short_token = re.sub(r"[^a-f0-9]", "", str(run_token).lower())[:10] or _safe_job_id(run_token)[:10]
    return f"{timestamp}__{_slugify_stem(paper_stem)}__{short_token}"


def build_friendly_artifact_label(title: str | None, source_name: str | None, run_token: UUID | str) -> str:
    base = title or Path(source_name or "paper").stem
    short_token = re.sub(r"[^a-f0-9]", "", str(run_token).lower())[:8] or _safe_job_id(run_token)[:8]
    return f"{_slugify_stem(base)}__{short_token}"


def build_paper_display_name(title: str | None, source_name: str | None, run_dir: Path | None = None) -> str:
    title_candidate = (title or "").strip()
    source_candidate = Path(source_name).stem if source_name else ""
    for candidate in (title_candidate, source_candidate, _run_label_display_stem(run_dir)):
        if candidate and not _looks_garbled_name(candidate):
            base = candidate
            break
    else:
        base = title_candidate or source_candidate or "未命名论文"
    return _safe_display_stem(base)


def index_path(job_id: UUID | str) -> Path:
    return job_index_root() / f"{_safe_job_id(job_id)}.json"


def initialize_run(job_id: UUID | str, paper_stem: str) -> Path:
    run_label = build_run_label(paper_stem, job_id)
    run_dir = jobs_root() / run_label
    run_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(
        index_path(job_id),
        {
            "run_dir": str(run_dir),
            "paper_stem": _slugify_stem(paper_stem),
            "paper_display": build_paper_display_name(paper_stem, None),
            "run_label": run_label,
        },
    )
    return run_dir


def _legacy_job_dir(job_id: UUID | str) -> Path:
    token = _safe_job_id(job_id)
    candidates = [
        get_settings().data_dir / "jobs" / token,
        get_settings().data_dir / token,
    ]
    for path in candidates:
        if path.exists():
            return path
    return get_settings().data_dir / token


def job_dir(job_id: UUID | str) -> Path:
    idx = index_path(job_id)
    if idx.exists():
        payload = read_json(idx)
        run_dir = Path(payload["run_dir"])
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir
    path = _legacy_job_dir(job_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_path(job_id: UUID | str) -> Path:
    return job_dir(job_id) / "job.json"


def events_path(job_id: UUID | str) -> Path:
    return job_dir(job_id) / "events.jsonl"


def reviews_dir(job_id: UUID | str) -> Path:
    path = job_dir(job_id) / "reviews"
    path.mkdir(parents=True, exist_ok=True)
    return path


def source_input_path(job_id: UUID | str, filename: str | None = None) -> Path:
    root = job_dir(job_id)
    if filename:
        suffix = Path(filename).suffix.lower() or ".bin"
        return root / f"source_input{suffix}"
    candidates = sorted(root.glob("source_input.*"))
    if candidates:
        return candidates[0]
    return root / "source_input.bin"


def ensure_artifact_paths(job_id: UUID | str) -> dict[str, Path]:
    root = job_dir(job_id)
    evidence = root / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    snapshots = evidence / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    return {
        "source_original": source_input_path(job_id),
        "normalized_markdown": evidence / "normalized.md",
        "plain_text": evidence / "plain_text.txt",
        "journal_requirements": evidence / "journal_requirements.md",
        "revision_notes": evidence / "revision_notes.md",
        "previous_review": evidence / "previous_review.md",
        "diagnostics": evidence / "diagnostics.json",
        "structured_content": evidence / "structured_content.json",
        "page_index": evidence / "page_index.json",
        "normalized_pdf": evidence / "normalized.pdf",
        "snapshots_dir": snapshots,
        "annotations": root / "annotations.json",
        "final_markdown": root / "final_report.md",
        "final_summary": root / "final_summary.json",
        "report_pdf": root / "final_report.pdf",
        "concerns_json": root / "concerns_table.json",
        "concerns_csv": root / "concerns_table.csv",
        "meta_review_json": root / "meta_review.json",
        "meta_review_md": root / "meta_review.md",
        "usage_summary": root / "usage_summary.json",
        "source_pdf": source_input_path(job_id),
        "mineru_markdown": evidence / "normalized.md",
        "mineru_content_list": evidence / "structured_content.json",
        "revision_response_review_md": root / "revision_response_review.md",
        "revision_response_review_json": root / "revision_response_review.json",
    }


def _copy_alias_file(source: Path, target: Path, *, written: dict[str, str], key: str) -> None:
    if not source.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)
    written[key] = str(target)


def _review_alias_stem(stem: str) -> str:
    mapping = {
        "committee_review_committee_reviewer_a": "11-委员会审稿-A",
        "committee_review_committee_reviewer_b": "12-委员会审稿-B",
        "committee_review_committee_reviewer_c": "13-委员会审稿-C",
        "specialist_review_theoretical": "21-理论专家审稿",
        "specialist_review_empirical": "22-方法专家审稿",
        "specialist_review_clarity": "23-表达专家审稿",
        "specialist_review_significance": "24-意义专家审稿",
        "specialist_review_structure": "25-结构专家审稿",
    }
    return mapping.get(stem, f"90-{_safe_display_stem(stem)}")


def _write_result_guide(
    target_dir: Path,
    *,
    paper_display: str,
    run_dir: Path,
    latest_dir: Path,
) -> Path:
    content = "\n".join(
        [
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
        ]
    ).strip() + "\n"
    guide_path = target_dir / "00-结果说明.txt"
    write_text_atomic(guide_path, content)
    return guide_path


def _copy_review_aliases(source_dir: Path, target_dir: Path, *, written: dict[str, str], key_prefix: str) -> None:
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    for source in sorted(source_dir.glob("*")):
        if source.is_dir():
            continue
        alias_stem = _review_alias_stem(source.stem)
        alias_path = target_dir / f"{alias_stem}{source.suffix.lower()}"
        _copy_alias_file(
            source,
            alias_path,
            written=written,
            key=f"{key_prefix}/{target_dir.name}/{alias_path.name}",
        )


def sync_latest_results_view_for_run_dir(
    run_dir: Path,
    *,
    title: str | None,
    source_name: str | None,
) -> dict[str, str]:
    paper_display = build_paper_display_name(title, source_name, run_dir)
    paper_dir = paper_views_root() / paper_display
    paper_dir.mkdir(parents=True, exist_ok=True)
    latest_dir = paper_dir / "最新结果"
    if latest_dir.exists():
        shutil.rmtree(latest_dir)
    latest_dir.mkdir(parents=True, exist_ok=True)

    written: dict[str, str] = {
        "paper_results_dir": str(paper_dir),
        "latest_results_dir": str(latest_dir),
    }
    guide_path = _write_result_guide(
        latest_dir,
        paper_display=paper_display,
        run_dir=run_dir,
        latest_dir=latest_dir,
    )
    written["latest/00-结果说明.txt"] = str(guide_path)

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

    # Conditional: 06-返修回应审稿.md only when revision_response_review.md exists
    revision_path = run_dir / "revision_response_review.md"
    if revision_path.exists():
        _copy_alias_file(
            revision_path,
            latest_dir / "06-返修回应审稿.md",
            written=written,
            key="latest/06-返修回应审稿.md",
        )

    reviews_source_dir = run_dir / "reviews"
    if reviews_source_dir.exists():
        _copy_review_aliases(
            reviews_source_dir,
            latest_dir / "10-Reviewer逐份意见",
            written=written,
            key_prefix="latest",
        )

    return written


def write_friendly_aliases_for_run_dir(
    run_dir: Path,
    *,
    title: str | None,
    source_name: str | None,
    run_token: UUID | str,
) -> dict[str, str]:
    del run_token
    prepare_only_note = run_dir / "00-当前仅完成预处理.txt"
    if prepare_only_note.exists():
        prepare_only_note.unlink()
    paper_display = build_paper_display_name(title, source_name, run_dir)
    latest_dir = paper_views_root() / paper_display / "最新结果"
    written: dict[str, str] = {}

    guide_path = _write_result_guide(
        run_dir,
        paper_display=paper_display,
        run_dir=run_dir,
        latest_dir=latest_dir,
    )
    written["run/00-结果说明.txt"] = str(guide_path)

    # Friendly aliases only exist in 按论文查看/ — no more in-run-dir duplicates.
    written.update(
        sync_latest_results_view_for_run_dir(
            run_dir,
            title=title,
            source_name=source_name,
        )
    )
    return written


def write_friendly_artifact_aliases(job_id: UUID | str, *, title: str | None, source_name: str | None) -> dict[str, str]:
    return write_friendly_aliases_for_run_dir(
        job_dir(job_id),
        title=title,
        source_name=source_name,
        run_token=job_id,
    )


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


def append_event(job_id: UUID | str, event: str, **extra: Any) -> None:
    row = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **extra}
    with events_path(job_id).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def save_job_state(job: FusionJobState) -> FusionJobState:
    with _LOCK:
        job.updated_at = datetime.now(timezone.utc)
        write_json_atomic(state_path(job.id), job.model_dump(mode="json"))
    return job


def load_job_state(job_id: UUID | str) -> FusionJobState | None:
    path = state_path(job_id)
    if not path.exists():
        return None
    with _LOCK:
        return FusionJobState.model_validate(read_json(path))


def mutate_job_state(job_id: UUID | str, fn: Callable[[FusionJobState], None]) -> FusionJobState:
    with _LOCK:
        state = load_job_state(job_id)
        if state is None:
            raise FileNotFoundError(f"Job not found: {job_id}")
        fn(state)
        state.updated_at = datetime.now(timezone.utc)
        write_json_atomic(state_path(job_id), state.model_dump(mode="json"))
    return state
