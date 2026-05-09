#!/usr/bin/env python3
"""Generate JSON Schema files from Pydantic models and write to skill references."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure fusion_reviewer is importable
_repo_root = Path(__file__).resolve().parents[1]
_src = _repo_root / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from fusion_reviewer.models import (
    AgentReview,
    EditorReport,
    FinalSummary,
    ManuscriptParadigm,
    PreviousConcern,
    RevisionAssessment,
    RevisionClaim,
    RevisionResponseReview,
)

SCHEMA_DIR = _repo_root / "skills" / "paper-review-committee" / "references" / "schemas"

SCHEMAS = {
    "reviewer.schema.json": AgentReview,
    "editor.schema.json": EditorReport,
    "manuscript_classification.schema.json": ManuscriptParadigm,
    "previous_concerns.schema.json": PreviousConcern,
    "revision_claims.schema.json": RevisionClaim,
    "revision_assessment.schema.json": RevisionAssessment,
    "revision_response_review.schema.json": RevisionResponseReview,
    "final_summary.schema.json": FinalSummary,
}


def main() -> int:
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    for filename, model in SCHEMAS.items():
        schema = model.model_json_schema()
        # Add metadata
        schema.setdefault("title", model.__name__)
        path = SCHEMA_DIR / filename
        path.write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"  wrote {filename}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
