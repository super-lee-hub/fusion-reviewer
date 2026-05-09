from __future__ import annotations

import json
from pathlib import Path

import pytest

from fusion_reviewer.classifier import (
    _build_taxonomy_table,
    build_classifier_prompt,
    classify_manuscript_from_response,
    parse_classification,
)
from fusion_reviewer.config import ParadigmCriteriaConfig, load_paradigm_criteria
from fusion_reviewer.models import FALLBACK_PARADIGM, EvidenceRef, ManuscriptParadigm, ParadigmLabel

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_build_classifier_prompt():
    prompt = build_classifier_prompt("Sample paper text about an experiment.")
    assert "experiment" in prompt.lower() or "research paradigm" in prompt.lower() or "Research paradigm" in prompt
    assert len(prompt) > 100


def test_parse_classification_empirical():
    payload = _load_json(FIXTURES / "classifier_empirical_response.json")
    page_index = {1: ["line 1", "line 2"]}
    paradigm = parse_classification(payload, page_index)
    assert paradigm.coarse_family in ("empirical", "mixed")


def test_parse_classification_theory():
    payload = _load_json(FIXTURES / "classifier_theory_response.json")
    page_index = {1: ["line 1", "line 2"]}
    paradigm = parse_classification(payload, page_index)
    assert paradigm.coarse_family in ("theoretical", "mixed")


def test_classify_from_response_fallback_on_empty():
    result = classify_manuscript_from_response("not valid json", {1: ["text"]})
    assert result is FALLBACK_PARADIGM


def test_build_taxonomy_table():
    criteria = load_paradigm_criteria()
    table = _build_taxonomy_table(criteria)
    assert "theoretical" in table.lower() or "empirical" in table.lower()
