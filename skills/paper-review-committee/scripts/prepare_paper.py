#!/usr/bin/env python3
"""Prepare shared evidence bundle for committee paper review.

Deterministic only — no LLM/API calls.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _ensure_import_paths() -> None:
    """Ensure fusion_reviewer package is importable."""
    try:
        import fusion_reviewer  # noqa: F401
        return
    except ImportError:
        pass
    # Search for repo root
    script_dir = Path(__file__).resolve().parents[1]  # skills/paper-review-committee/
    for candidate in [script_dir.parents[2], Path.cwd()]:  # repo root
        src = candidate / "src"
        if (src / "fusion_reviewer").exists():
            sys.path.insert(0, str(src))
            return
    print("error: fusion_reviewer core package not importable. Run: pip install -e .", file=sys.stderr)
    raise SystemExit(4)


_ensure_import_paths()

from fusion_reviewer.artifact_writer import (  # noqa: E402
    create_run_directory,
    ensure_evidence_paths,
    write_json_atomic,
    write_text_atomic,
)
from fusion_reviewer.normalization import normalize_document  # noqa: E402


def _read_optional_file(path: str | None) -> str | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        print(f"warning: file not found: {path}", file=sys.stderr)
        return None
    return p.read_text(encoding="utf-8", errors="replace")


def _copy_file_to(src: Path, dest_dir: Path, name: str) -> Path | None:
    if not src.exists():
        return None
    dest = dest_dir / name
    dest.write_bytes(src.read_bytes())
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper", required=True, help="Path to paper file (PDF/DOCX/DOC)")
    parser.add_argument("--output-root", default=None, help="Output root directory")
    parser.add_argument("--run-id", default=None, help="Fixed run ID")
    parser.add_argument("--force", action="store_true", help="Force overwrite existing run directory (requires --run-id)")
    parser.add_argument("--journal-text", default=None, help="Journal requirements as text")
    parser.add_argument("--journal-file", default=None, help="Journal requirements file path")
    parser.add_argument("--revision-file", default=None, help="Revision notes / response letter path")
    parser.add_argument("--previous-review-file", default=None, help="Previous review file (takes priority over --previous-review-dir)")
    parser.add_argument("--previous-review-dir", default=None, help="Previous review output directory for auto-discovery")
    parser.add_argument("--previous-concerns-file", default=None, help="Structured previous concerns JSON")
    parser.add_argument("--revision-claims-file", default=None, help="Structured revision claims JSON")
    parser.add_argument("--original-paper", default=None, help="Original paper for before/after comparison")
    args = parser.parse_args(argv)

    paper_path = Path(args.paper)
    if not paper_path.exists():
        print(f"error: paper file not found: {args.paper}", file=sys.stderr)
        return 2

    # Determine output root
    if args.output_root:
        output_root = Path(args.output_root)
    else:
        from fusion_reviewer.config import get_settings
        output_root = get_settings().data_dir
    output_root.mkdir(parents=True, exist_ok=True)

    # Validate --force usage
    if args.force and not args.run_id:
        print("error: --force requires explicit --run-id", file=sys.stderr)
        return 2

    # Generate or use provided run ID
    run_id = args.run_id or f"run_{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"

    # Create run directory
    try:
        run_dir = create_run_directory(output_root, run_id, force=args.force)
    except FileExistsError:
        print(f"error: run directory already exists: {output_root / run_id}", file=sys.stderr)
        print("Use --run-id with --force to overwrite, or omit --run-id for unique directory.", file=sys.stderr)
        return 2

    paths = ensure_evidence_paths(run_dir)
    print(f"Run directory: {run_dir}", file=sys.stderr)

    # Copy paper
    source_copy_path = paths["source_copy"].with_suffix(paper_path.suffix)
    import shutil
    shutil.copy2(paper_path, source_copy_path)

    # Normalize document
    print("Normalizing document...", file=sys.stderr)
    try:
        evidence = normalize_document(paper_path)
    except Exception as exc:
        print(f"error: document normalization failed: {exc}", file=sys.stderr)
        return 3

    # Write normalized outputs
    if evidence.markdown:
        write_text_atomic(paths["normalized_md"], evidence.markdown)
    if evidence.plain_text:
        write_text_atomic(paths["plain_text"], evidence.plain_text)
    if hasattr(evidence, "page_index") and evidence.page_index:
        write_json_atomic(paths["page_index"], evidence.page_index)
    if hasattr(evidence, "structured_pages"):
        write_json_atomic(paths["structured_content"], list(evidence.structured_pages) if evidence.structured_pages else [])
    if hasattr(evidence, "diagnostics") and evidence.diagnostics:
        write_json_atomic(paths["diagnostics"], evidence.diagnostics)

    # Copy snapshot files if available
    if hasattr(evidence, "snapshot_paths") and evidence.snapshot_paths:
        snapshots_dir = paths["snapshots_dir"]
        for sp in evidence.snapshot_paths:
            sp_path = Path(sp) if isinstance(sp, str) else sp
            if sp_path.exists():
                _copy_file_to(sp_path, snapshots_dir, sp_path.name)

    # Journal requirements
    journal_parts = []
    if args.journal_text:
        journal_parts.append(args.journal_text.strip())
    if args.journal_file:
        jf_content = _read_optional_file(args.journal_file)
        if jf_content:
            journal_parts.append(jf_content.strip())
    if journal_parts:
        write_text_atomic(
            paths["journal_requirements"],
            "\n\n---\n\n".join(journal_parts) + "\n",
        )

    # Revision notes
    if args.revision_file:
        rev_content = _read_optional_file(args.revision_file)
        if rev_content:
            write_text_atomic(paths["revision_notes"], rev_content)

    # Previous review
    prev_content: str | None = None
    if args.previous_review_file:
        prev_content = _read_optional_file(args.previous_review_file)
    elif args.previous_review_dir:
        prev_dir = Path(args.previous_review_dir)
        if prev_dir.exists():
            candidates = sorted(
                list(prev_dir.glob("final_report.md")) +
                list(prev_dir.glob("meta_review.md"))
            )
            if candidates:
                prev_content = candidates[0].read_text(encoding="utf-8", errors="replace")
    if prev_content:
        write_text_atomic(paths["previous_review"], prev_content)

    # Structured revision files
    for arg_name, dest_name in [
        ("previous_concerns_file", "previous_concerns.json"),
        ("revision_claims_file", "revision_claims.json"),
    ]:
        file_path = getattr(args, arg_name, None)
        if file_path:
            content = _read_optional_file(file_path)
            if content:
                write_text_atomic(paths["evidence_dir"] / dest_name, content)

    # Original paper
    if args.original_paper:
        orig_path = Path(args.original_paper)
        if orig_path.exists():
            orig_dir = paths["original_dir"]
            _copy_file_to(orig_path, orig_dir, f"source_copy{orig_path.suffix}")
            try:
                orig_evidence = normalize_document(orig_path)
                write_text_atomic(orig_dir / "normalized.md", orig_evidence.markdown or "")
                if hasattr(orig_evidence, "page_index") and orig_evidence.page_index:
                    write_json_atomic(orig_dir / "page_index.json", orig_evidence.page_index)
                if hasattr(orig_evidence, "diagnostics") and orig_evidence.diagnostics:
                    write_json_atomic(orig_dir / "diagnostics.json", orig_evidence.diagnostics)
            except Exception as exc:
                print(f"warning: original paper normalization failed: {exc}", file=sys.stderr)

    # Write run manifest
    manifest = {
        "run_id": run_id,
        "paper_path": str(paper_path),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "journal_present": bool(journal_parts),
        "revision_present": bool(args.revision_file),
        "original_paper_present": bool(args.original_paper),
        "evidence_paths": {k: str(v) for k, v in paths.items()},
        "environment_status": {
            "fusion_reviewer_importable": True,
            "libreoffice_available": _check_libreoffice(),
        },
    }
    write_json_atomic(run_dir / "run_manifest.json", manifest)

    # Environment summary
    print("Environment:", file=sys.stderr)
    print(f"  fusion_reviewer importable: True", file=sys.stderr)
    print(f"  LibreOffice available: {_check_libreoffice()}", file=sys.stderr)
    print(f"  journal requirements: {'present' if journal_parts else 'absent'}", file=sys.stderr)
    print(f"  revision context: {'present' if args.revision_file else 'absent'}", file=sys.stderr)
    print(f"  original paper: {'present' if args.original_paper else 'absent'}", file=sys.stderr)

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def _check_libreoffice() -> bool:
    import shutil
    return shutil.which("soffice") is not None or shutil.which("libreoffice") is not None


if __name__ == "__main__":
    raise SystemExit(main())
