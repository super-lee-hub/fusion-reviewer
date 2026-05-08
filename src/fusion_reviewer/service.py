from __future__ import annotations

import json
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from deepreview.types import JobStatus

from .document_io import detect_document_kind, extract_docx_text
from .models import FusionJobState
from .orchestration import process_job
from .storage import (
    append_event,
    ensure_artifact_paths,
    initialize_run,
    job_index_root,
    job_dir,
    load_job_state,
    reviews_dir,
    save_job_state,
    source_input_path,
)
from .text_utils import decode_text_bytes


def _decode_journal_file(*, filename: str, file_bytes: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".docx":
        with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as handle:
            temp_path = Path(handle.name)
            handle.write(file_bytes)
        try:
            return extract_docx_text(temp_path).strip()
        finally:
            temp_path.unlink(missing_ok=True)
    return decode_text_bytes(file_bytes).strip()


def _combine_journal_requirements(
    *,
    journal_text: str | None = None,
    journal_file_bytes: bytes | None = None,
    journal_filename: str | None = None,
) -> tuple[str | None, str | None]:
    sections: list[str] = []
    source_parts: list[str] = []
    if journal_text and journal_text.strip():
        sections.extend(["# 期刊要求", "", journal_text.strip()])
        source_parts.append("text")
    if journal_file_bytes:
        filename = journal_filename or "journal_requirements.txt"
        decoded = _decode_journal_file(filename=filename, file_bytes=journal_file_bytes)
        if decoded:
            if sections:
                sections.extend(["", "---", ""])
            sections.extend([f"# 期刊要求文件：{filename}", "", decoded])
            source_parts.append(f"file:{filename}")
    if not sections:
        return None, None
    return "\n".join(sections).strip() + "\n", ", ".join(source_parts)


def _combine_revision_notes(
    *,
    revision_text: str | None = None,
    revision_file_bytes: bytes | None = None,
    revision_filename: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Decode and combine revision materials. Returns (markdown, source, quality)."""
    sections: list[str] = []
    source_parts: list[str] = []
    quality: str | None = None

    if revision_text and revision_text.strip():
        sections.append(revision_text.strip())
        source_parts.append("text")

    if revision_file_bytes:
        filename = revision_filename or "revision_notes.txt"
        suffix = Path(filename).suffix.lower()
        if suffix == ".docx":
            with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as handle:
                temp_path = Path(handle.name)
                handle.write(revision_file_bytes)
            try:
                decoded = extract_docx_text(temp_path).strip()
            finally:
                temp_path.unlink(missing_ok=True)
        else:
            decoded = decode_text_bytes(revision_file_bytes).strip()
        if decoded:
            if sections:
                sections.extend(["", "---", ""])
            sections.append(decoded)
            source_parts.append(f"file:{filename}")

    if not sections:
        return None, None, None

    combined = "\n".join(sections).strip()
    # Quality check
    if "\ufffd" in combined:
        quality = "garbled"
    else:
        quality = "good"

    return combined + "\n", ", ".join(source_parts), quality


class JobService:
    def submit_document(
        self,
        *,
        file_bytes: bytes,
        filename: str,
        title: str | None = None,
        provider_override: str | None = None,
        mode: str = "backend",
        journal_text: str | None = None,
        journal_file_bytes: bytes | None = None,
        journal_filename: str | None = None,
        revision_text: str | None = None,
        revision_file_bytes: bytes | None = None,
        revision_filename: str | None = None,
    ) -> FusionJobState:
        document_type = detect_document_kind(Path(filename))
        journal_markdown, journal_source = _combine_journal_requirements(
            journal_text=journal_text,
            journal_file_bytes=journal_file_bytes,
            journal_filename=journal_filename,
        )
        revision_markdown, revision_source, revision_quality = _combine_revision_notes(
            revision_text=revision_text,
            revision_file_bytes=revision_file_bytes,
            revision_filename=revision_filename,
        )
        job = FusionJobState(
            title=title or Path(filename).stem,
            source_name=filename,
            source_pdf_name=filename,
            document_type=document_type,
            mode=mode,
            provider_override=provider_override,
            message="Job queued." if mode == "backend" else "Codex review run prepared.",
            journal_context_present=bool(journal_markdown),
            journal_context_source=journal_source,
            revision_context_present=bool(revision_markdown),
            revision_context_source=revision_source,
            revision_extraction_quality=revision_quality,
        )
        run_dir = initialize_run(job.id, title or Path(filename).stem)
        job.run_label = run_dir.name
        source_path = source_input_path(job.id, filename)
        source_path.write_bytes(file_bytes)
        paths = ensure_artifact_paths(job.id)
        if journal_markdown:
            paths["journal_requirements"].write_text(journal_markdown, encoding="utf-8")
        if revision_markdown:
            paths["revision_notes"].write_text(revision_markdown, encoding="utf-8")
        job.artifacts.source_pdf_path = str(source_path)
        job.metadata["source_input_path"] = str(source_path)
        job.metadata["run_dir"] = str(run_dir)
        job.metadata["run_label"] = run_dir.name
        if journal_markdown:
            job.metadata["journal_requirements_path"] = str(paths["journal_requirements"])
            job.metadata["journal_context_source"] = journal_source
        if revision_markdown:
            job.metadata["revision_notes_path"] = str(paths["revision_notes"])
            job.metadata["revision_context_source"] = revision_source
            job.metadata["revision_extraction_quality"] = revision_quality
        save_job_state(job)
        append_event(
            job.id,
            "submitted",
            source_name=filename,
            document_type=document_type,
            mode=mode,
            provider_override=provider_override,
            journal_context_present=bool(journal_markdown),
            journal_context_source=journal_source,
            revision_context_present=bool(revision_markdown),
            revision_context_source=revision_source,
            revision_extraction_quality=revision_quality,
        )
        if revision_markdown:
            append_event(
                job.id,
                "revision_context_detected",
                source=revision_source,
                quality=revision_quality,
            )
        if mode == "backend":
            thread = threading.Thread(target=process_job, args=(str(job.id),), daemon=True)
            thread.start()
        return load_job_state(job.id) or job

    def submit_file(
        self,
        *,
        paper_path: Path,
        title: str | None = None,
        provider_override: str | None = None,
        mode: str = "backend",
        journal_text: str | None = None,
        journal_file_path: Path | None = None,
        revision_text: str | None = None,
        revision_file_path: Path | None = None,
    ) -> FusionJobState:
        return self.submit_document(
            file_bytes=paper_path.read_bytes(),
            filename=paper_path.name,
            title=title or paper_path.stem,
            provider_override=provider_override,
            mode=mode,
            journal_text=journal_text,
            journal_file_bytes=journal_file_path.read_bytes() if journal_file_path else None,
            journal_filename=journal_file_path.name if journal_file_path else None,
            revision_text=revision_text,
            revision_file_bytes=revision_file_path.read_bytes() if revision_file_path else None,
            revision_filename=revision_file_path.name if revision_file_path else None,
        )

    def get_status(self, job_id: str) -> FusionJobState | None:
        return load_job_state(job_id)

    def list_jobs(self, limit: int = 30) -> list[FusionJobState]:
        jobs: list[FusionJobState] = []
        index_files = sorted(job_index_root().glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        for idx_file in index_files:
            state = load_job_state(idx_file.stem)
            if state is None:
                continue
            if not state.run_label:
                state.run_label = job_dir(state.id).name
            jobs.append(state)
            if len(jobs) >= limit:
                break
        return jobs

    def wait(self, job_id: str, timeout_seconds: int) -> FusionJobState | None:
        deadline = time.time() + max(0, timeout_seconds)
        while time.time() <= deadline:
            state = load_job_state(job_id)
            if state is None:
                return None
            if state.status in {JobStatus.completed, JobStatus.failed}:
                return state
            time.sleep(1)
        return load_job_state(job_id)

    def artifacts(self, job_id: str) -> dict[str, Any]:
        state = load_job_state(job_id)
        if state is None:
            raise FileNotFoundError(f"Job not found: {job_id}")
        root = job_dir(job_id)
        output: dict[str, Any] = {"job_id": job_id, "status": state.status, "artifacts": {}}
        for path in sorted(root.rglob("*")):
            if path.is_dir():
                continue
            relative_name = path.relative_to(root).as_posix()
            output["artifacts"][relative_name] = {
                "path": str(path),
                "size_bytes": path.stat().st_size,
            }
        return output

    def get_review(self, job_id: str, agent_id: str) -> dict[str, Any]:
        root = reviews_dir(job_id)
        for path in root.glob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("agent_id") == agent_id:
                return payload
        raise FileNotFoundError(f"Review not found for agent {agent_id}")

    def result(self, job_id: str) -> dict[str, Any]:
        state = load_job_state(job_id)
        if state is None:
            raise FileNotFoundError(f"Job not found: {job_id}")
        paths = ensure_artifact_paths(job_id)
        payload: dict[str, Any] = {"job": state.model_dump(mode="json")}
        if paths["final_markdown"].exists():
            payload["final_report_markdown"] = paths["final_markdown"].read_text(encoding="utf-8")
        if paths["meta_review_json"].exists():
            payload["meta_review"] = json.loads(paths["meta_review_json"].read_text(encoding="utf-8"))
        if paths["concerns_json"].exists():
            payload["concerns"] = json.loads(paths["concerns_json"].read_text(encoding="utf-8"))
        if paths["final_summary"].exists():
            payload["final_summary"] = json.loads(paths["final_summary"].read_text(encoding="utf-8"))
        return payload
