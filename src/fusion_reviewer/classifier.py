from __future__ import annotations

import json
from typing import Any

from .config import ParadigmCriteriaConfig, get_settings
from .models import FALLBACK_PARADIGM, EvidenceRef, ManuscriptParadigm, ParadigmLabel


def _build_taxonomy_table(criteria: ParadigmCriteriaConfig) -> str:
    lines = ["## Research paradigm taxonomy", ""]
    current_family = None
    for item in criteria.paradigms:
        if item.coarse_family != current_family:
            current_family = item.coarse_family
            lines.append(f"### {current_family}")
        lines.append(f"- {item.tag}: {', '.join(item.appropriate_focus[:3])}")
    return "\n".join(lines)


def _build_classifier_prompt(indexed_text: str, criteria: ParadigmCriteriaConfig) -> str:
    taxonomy = _build_taxonomy_table(criteria)
    return f"""
You are a research methodology classifier. Classify the manuscript's research paradigm using only the indexed evidence below.

Return valid JSON with this exact schema:
{{
  "coarse_family": "empirical|theoretical|mixed|review_synthesis",
  "paradigm_labels": [
    {{
      "label": "formal_modeling",
      "confidence": 0.85,
      "primary": true,
      "evidence_refs": [
        {{"page": 1, "start_line": 1, "end_line": 2, "quote": "exact quote from indexed evidence"}}
      ]
    }}
  ],
  "rationale": "concise explanation of classification basis"
}}

Rules:
- Assign at least one paradigm label with evidence.
- confidence must be between 0.0 and 1.0.
- Exactly one label should have primary: true.
- Every label must cite at least one evidence_ref with page/line/quote from the indexed evidence.
- Quote must match the indexed evidence exactly.
- Use ONLY labels from the taxonomy below. If the paper does not fit any label perfectly, choose the closest match and note this in rationale.
- For papers mixing multiple paradigms, use coarse_family "mixed" and list all applicable labels.

{taxonomy}

Indexed evidence:
{indexed_text}
""".strip()


def _validate_refs(
    labels: list[dict[str, Any]],
    page_index: dict[int, list[str]],
) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for raw_label in labels:
        refs = raw_label.get("evidence_refs", [])
        valid_refs = []
        for ref in refs:
            page = ref.get("page")
            if page is None:
                continue
            page = int(page)
            if page in page_index:
                valid_refs.append(ref)
        if not valid_refs:
            raw_label["confidence"] = min(float(raw_label.get("confidence", 0.5)), 0.4)
            raw_label["evidence_refs"] = []
        else:
            raw_label["evidence_refs"] = valid_refs
        cleaned.append(raw_label)
    return cleaned


def parse_classification(
    payload: dict[str, Any],
    page_index: dict[int, list[str]],
) -> ManuscriptParadigm:
    coarse_family = str(payload.get("coarse_family") or "mixed")
    raw_labels = payload.get("paradigm_labels", [])
    if not isinstance(raw_labels, list) or not raw_labels:
        raise ValueError("paradigm_labels must be a non-empty list")

    raw_labels = _validate_refs(raw_labels, page_index)

    if all(not (lb.get("evidence_refs") or []) for lb in raw_labels):
        return FALLBACK_PARADIGM

    paradigm_labels: list[ParadigmLabel] = []
    for raw in raw_labels:
        refs = [
            EvidenceRef(
                page=int(r.get("page", 1)) if r.get("page") is not None else None,
                start_line=int(r.get("start_line", 1)) if r.get("start_line") is not None else None,
                end_line=int(r.get("end_line", 1)) if r.get("end_line") is not None else None,
                quote=str(r.get("quote") or ""),
            )
            for r in (raw.get("evidence_refs") or [])
        ]
        paradigm_labels.append(
            ParadigmLabel(
                label=str(raw.get("label") or ""),
                confidence=float(raw.get("confidence", 0.5)),
                primary=bool(raw.get("primary", False)),
                evidence_refs=refs,
            )
        )

    return ManuscriptParadigm(
        coarse_family=coarse_family,
        paradigm_labels=paradigm_labels,
        rationale=str(payload.get("rationale") or ""),
    )


def build_classifier_prompt(
    indexed_text: str,
    criteria: ParadigmCriteriaConfig | None = None,
) -> str:
    """Build the classifier prompt. The host agent calls the LLM with this prompt."""
    if criteria is None:
        from .config import load_paradigm_criteria
        criteria = load_paradigm_criteria()
    return _build_classifier_prompt(indexed_text, criteria)


def classify_manuscript_from_response(
    response_text: str,
    page_index: dict[int, list[str]],
    *,
    criteria: ParadigmCriteriaConfig | None = None,
) -> ManuscriptParadigm:
    """Parse LLM response into ManuscriptParadigm. Deterministic — no API call."""
    settings = get_settings()

    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError:
        return FALLBACK_PARADIGM

    paradigm = parse_classification(payload, page_index)

    if not paradigm.paradigm_labels:
        return FALLBACK_PARADIGM

    primary = next((lb for lb in paradigm.paradigm_labels if lb.primary), None)
    if primary is None and paradigm.paradigm_labels:
        paradigm.paradigm_labels[0].primary = True
        primary = paradigm.paradigm_labels[0]

    if primary is not None:
        if primary.confidence >= settings.classifier_confidence_accept:
            pass
        elif primary.confidence >= settings.classifier_confidence_reject:
            paradigm.rationale = f"[LOW CONFIDENCE] {paradigm.rationale}"
        else:
            return FALLBACK_PARADIGM

    return paradigm
