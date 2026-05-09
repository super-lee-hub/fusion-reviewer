#!/usr/bin/env python3
"""Finalize review artifacts from reviewer/editor JSON — deterministic only, no LLM/API calls."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _ensure_import_paths() -> None:
    try:
        import fusion_reviewer  # noqa: F401
        return
    except ImportError:
        pass
    script_dir = Path(__file__).resolve().parents[1]
    for candidate in [script_dir.parents[2], Path.cwd()]:
        src = candidate / "src"
        if (src / "fusion_reviewer").exists():
            sys.path.insert(0, str(src))
            return
    print("error: fusion_reviewer core package not importable. Run: pip install -e .", file=sys.stderr)
    raise SystemExit(4)


_ensure_import_paths()

from fusion_reviewer.artifact_writer import (  # noqa: E402
    read_json,
    sync_latest_results_view,
    write_concerns_csv,
    write_concerns_json,
    write_final_report,
    write_final_summary,
    write_meta_review,
    write_revision_assessment,
    write_revision_response_review,
    write_text_atomic,
)
from fusion_reviewer.concern_merge import merge_concerns  # noqa: E402
from fusion_reviewer.models import (  # noqa: E402
    AgentReview,
    EditorReport,
    FinalSummary,
    RevisionAssessment,
    RevisionResponseReview,
)
from fusion_reviewer.quote_verifier import validate_revision_assessment_evidence  # noqa: E402
from fusion_reviewer.reports import build_final_report, build_final_summary  # noqa: E402
from fusion_reviewer.schema_validator import (  # noqa: E402
    validate_editor_output,
    validate_reviewer_output,
)


def _load_reviews_from_dir(reviews_dir: Path) -> list[AgentReview]:
    reviews: list[AgentReview] = []
    if not reviews_dir.exists():
        return reviews
    for json_path in sorted(reviews_dir.glob("*.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            # Validate against schema
            diagnostics = validate_reviewer_output(data)
            if not diagnostics.get("valid", True):
                print(f"warning: schema validation issues in {json_path.name}: {diagnostics.get('errors', [])}", file=sys.stderr)
            reviews.append(AgentReview.model_validate(data))
        except Exception as exc:
            print(f"warning: failed to load {json_path.name}: {exc}", file=sys.stderr)
    return reviews


def _determine_committee_mode(review_count: int) -> str:
    if review_count == 0:
        return "draft_only"  # will fail downstream
    if review_count <= 2:
        return "draft_only"
    if review_count <= 7:
        return "partial"
    return "full"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, help="Run directory from prepare_paper.py")
    parser.add_argument("--reviews-dir", default=None, help="Directory containing reviewer JSONs")
    parser.add_argument("--editor-file", default=None, help="Path to editor synthesis JSON")
    parser.add_argument("--revision-response-file", default=None, help="Path to revision response review JSON")
    parser.add_argument("--strict", action="store_true", help="Fail on any schema violation or evidence issue")
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir)
    if not run_dir.exists():
        print(f"error: run directory not found: {run_dir}", file=sys.stderr)
        return 2

    reviews_dir = Path(args.reviews_dir) if args.reviews_dir else (run_dir / "reviews")
    print(f"Loading reviews from: {reviews_dir}", file=sys.stderr)

    reviews = _load_reviews_from_dir(reviews_dir)
    completed_reviews = [r for r in reviews if r.status == "completed"]

    if not reviews:
        print("error: no reviewer JSONs found (0 reviewers = cannot proceed)", file=sys.stderr)
        return 2

    committee_mode = _determine_committee_mode(len(completed_reviews))
    print(f"Committee mode: {committee_mode} ({len(completed_reviews)} completed reviewers)", file=sys.stderr)

    # Merge concerns
    concerns = merge_concerns(completed_reviews)
    print(f"Merged concerns: {len(concerns)}", file=sys.stderr)

    # Load or draft editor synthesis
    editor_synthesis_present = False
    if args.editor_file:
        editor_path = Path(args.editor_file)
        if editor_path.exists():
            editor_data = json.loads(editor_path.read_text(encoding="utf-8"))
            if args.strict:
                diag = validate_editor_output(editor_data)
                if not diag.get("valid", True):
                    print(f"error: editor schema validation failed: {diag.get('errors', [])}", file=sys.stderr)
                    return 1
            editor = EditorReport.model_validate(editor_data)
            editor_synthesis_present = True
        else:
            print(f"error: editor file not found: {args.editor_file}", file=sys.stderr)
            return 2
    else:
        # Draft mode — no editor synthesis
        editor = EditorReport(
            decision="major_revision",
            decision_rationale="Draft mode — no editor synthesis provided.",
        )
        print("No --editor-file provided — generating draft_no_editor_synthesis output", file=sys.stderr)

    # Revision response
    revision_response_present = False
    revision_response_review: RevisionResponseReview | None = None
    if args.revision_response_file:
        rr_path = Path(args.revision_response_file)
        if rr_path.exists():
            rr_data = json.loads(rr_path.read_text(encoding="utf-8"))
            revision_response_review = RevisionResponseReview.model_validate(rr_data)
            revision_response_present = True

    # Load page index for evidence verification
    page_index_path = run_dir / "evidence" / "page_index.json"
    page_index: dict = {}
    if page_index_path.exists():
        try:
            page_index = json.loads(page_index_path.read_text(encoding="utf-8"))
            # Convert string keys (from JSON) to int keys
            page_index = {int(k): v for k, v in page_index.items()}
        except Exception:
            pass

    # Validate revision assessments
    diagnostics: list[str] = []
    tolerant_issues: list[str] = []
    if revision_response_review:
        for assessment in revision_response_review.assessments:
            valid, downgraded = validate_revision_assessment_evidence(
                assessment.status, assessment.evidence_refs
            )
            if not valid:
                msg = f"Revision assessment {assessment.concern_id}: status 'addressed' downgraded to '{downgraded}' — no manuscript evidence ref"
                if args.strict:
                    print(f"error: {msg}", file=sys.stderr)
                    return 1
                tolerant_issues.append(msg)
                assessment.status = downgraded

    # Build final report
    run_manifest_path = run_dir / "run_manifest.json"
    run_manifest = {}
    if run_manifest_path.exists():
        run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    title = run_manifest.get("title", Path(run_manifest.get("paper_path", "paper")).stem)
    source_name = Path(run_manifest.get("paper_path", "paper")).name

    journal_requirements = None
    jr_path = run_dir / "evidence" / "journal_requirements.md"
    if jr_path.exists():
        journal_requirements = jr_path.read_text(encoding="utf-8")

    final_markdown = build_final_report(
        title=title,
        reviews=completed_reviews,
        concerns=concerns,
        editor=editor,
        journal_requirements=journal_requirements,
        revision_review_result=revision_response_review,
        revision_context_present=run_manifest.get("revision_present", False),
    )

    final_summary = build_final_summary(
        run_id=run_dir.name,
        title=title,
        source_name=source_name,
        reviews=completed_reviews,
        concerns=concerns,
        editor=editor,
        state_metadata={
            "layout_fidelity": run_manifest.get("layout_fidelity"),
            "extractor_used": run_manifest.get("extractor_used"),
            "conversion_used": run_manifest.get("conversion_used"),
            "journal_context_present": bool(journal_requirements),
            "journal_context_source": run_manifest.get("journal_context_source"),
            "mineru_attempted": None,
            "mineru_succeeded": None,
            "revision_context_present": run_manifest.get("revision_present", False),
            "revision_context_source": None,
            "revision_extraction_quality": None,
        },
    )

    # Add final summary fields
    final_summary_obj = FinalSummary(
        run_id=run_dir.name,
        title=title,
        source_name=source_name,
        decision=editor.decision,
        concerns_count=len(concerns),
        completed_reviews=len(completed_reviews),
        failed_reviews=len(reviews) - len(completed_reviews),
        committee_mode=committee_mode,
        editor_mode="present" if editor_synthesis_present else "draft_only",
        revision_mode="full_synthesis" if revision_response_present else ("none" if not run_manifest.get("revision_present") else "reviewer_assessments_only"),
        editor_synthesis_present=editor_synthesis_present,
        revision_response_present=revision_response_present,
        provenance_summary={
            "subagent": sum(1 for r in reviews if r.review_source == "subagent"),
            "local": sum(1 for r in reviews if r.review_source == "local"),
            "unknown": sum(1 for r in reviews if r.review_source == "unknown"),
        },
        layout_fidelity=final_summary.get("layout_fidelity"),
        extractor_used=final_summary.get("extractor_used"),
        conversion_used=final_summary.get("conversion_used"),
        journal_context_present=bool(journal_requirements),
        revision_context_present=run_manifest.get("revision_present", False),
        reviewers=[
            {
                "agent_id": r.agent_id,
                "kind": r.kind,
                "recommendation": r.recommendation,
                "status": r.status,
                "review_source": r.review_source,
            }
            for r in reviews
        ],
    )

    # Write artifacts
    write_final_report(run_dir / "final_report.md", final_markdown)
    write_meta_review(run_dir / "meta_review.json", editor.model_dump(mode="json"))
    write_text_atomic(run_dir / "meta_review.md", editor.markdown or f"# {editor.title}\n\n{editor.decision_rationale}")
    write_concerns_json(run_dir / "concerns_table.json", [c.model_dump(mode="json") for c in concerns])
    write_concerns_csv(run_dir / "concerns_table.csv", [c.model_dump(mode="json") for c in concerns])
    write_final_summary(run_dir / "final_summary.json", final_summary_obj.model_dump(mode="json"))

    if revision_response_review:
        write_revision_response_review(
            run_dir / "revision_response_review.json",
            revision_response_review.model_dump(mode="json"),
        )
        if revision_response_review.markdown:
            write_text_atomic(run_dir / "revision_response_review.md", revision_response_review.markdown)

    # Write diagnostics
    import json as _json
    from fusion_reviewer.artifact_writer import write_json_atomic
    write_json_atomic(
        run_dir / "evidence" / "finalize_diagnostics.json",
        {
            "tolerant_issues": tolerant_issues,
            "committee_mode": committee_mode,
            "editor_synthesis_present": editor_synthesis_present,
            "schema_validation": {"reviewers_loaded": len(reviews), "completed": len(completed_reviews)},
        },
    )

    # Generate friendly aliases
    alias_info = sync_latest_results_view(
        run_dir,
        title=title,
        source_name=source_name,
        output_root=run_dir.parent if run_dir.parent.name != run_dir.name else run_dir.parent.parent,
    )

    print(f"Finalized: {run_dir}", file=sys.stderr)
    print(f"  final_report.md, meta_review.md, concerns_table.csv, final_summary.json", file=sys.stderr)
    print(f"  committee mode: {committee_mode}", file=sys.stderr)
    print(f"  editor synthesis: {'present' if editor_synthesis_present else 'draft_only'}", file=sys.stderr)
    if tolerant_issues:
        print(f"  tolerant issues: {len(tolerant_issues)}", file=sys.stderr)
        for issue in tolerant_issues:
            print(f"    - {issue}", file=sys.stderr)

    result = {
        "run_dir": str(run_dir),
        "committee_mode": committee_mode,
        "editor_synthesis_present": editor_synthesis_present,
        "concerns_count": len(concerns),
        "completed_reviews": len(completed_reviews),
        "decision": editor.decision,
        "tolerant_issues": tolerant_issues,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
