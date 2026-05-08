from __future__ import annotations

import json
from pathlib import Path

import pytest

from fusion_reviewer.classifier import (
    _build_taxonomy_table,
    classify_manuscript,
    parse_classification,
)
from fusion_reviewer.config import ParadigmCriteriaConfig
from fusion_reviewer.models import FALLBACK_PARADIGM, ManuscriptParadigm
from fusion_reviewer.providers import MockProvider

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _sample_page_index() -> dict[int, list[str]]:
    return {1: ["Abstract text", "Introduction text", "Model setup text", "Proposition text"], 2: ["Results text", "Conclusion text"]}


def _sample_criteria() -> ParadigmCriteriaConfig:
    return ParadigmCriteriaConfig(
        paradigms=[
            {"tag": "formal_modeling", "coarse_family": "theoretical", "appropriate_focus": ["模型假设", "推导正确性"], "inappropriate_critique_patterns": ["实证识别"]},
            {"tag": "experiment", "coarse_family": "empirical", "appropriate_focus": ["识别策略", "随机化"], "inappropriate_critique_patterns": ["模型推导"]},
        ],
        fallback_focus=["方法适切性", "论证自洽性"],
    )


class TestParseClassification:
    def test_valid(self):
        payload = _load_json("classifier_theory_response.json")
        result = parse_classification(payload, _sample_page_index())
        assert result.coarse_family == "theoretical"
        assert len(result.paradigm_labels) == 1
        assert result.paradigm_labels[0].label == "formal_modeling"
        assert result.paradigm_labels[0].confidence == 0.85
        assert result.paradigm_labels[0].primary is True

    def test_missing_evidence_and_all_dropped(self):
        payload = {
            "coarse_family": "theoretical",
            "paradigm_labels": [
                {
                    "label": "formal_modeling",
                    "confidence": 0.85,
                    "primary": True,
                    "evidence_refs": [{"page": 99, "start_line": 1, "end_line": 2, "quote": "nonexistent"}],
                }
            ],
            "rationale": "test",
        }
        result = parse_classification(payload, _sample_page_index())
        assert result is FALLBACK_PARADIGM

    def test_unknown_family_handled_gracefully(self):
        payload = {
            "coarse_family": "unknown_type",
            "paradigm_labels": [
                {
                    "label": "something_odd",
                    "confidence": 0.7,
                    "primary": True,
                    "evidence_refs": [{"page": 1, "start_line": 1, "end_line": 2, "quote": "Abstract text"}],
                }
            ],
            "rationale": "unusual paper",
        }
        result = parse_classification(payload, _sample_page_index())
        assert result.coarse_family == "unknown_type"
        assert len(result.paradigm_labels) == 1

    def test_multi_label(self):
        payload = {
            "coarse_family": "mixed",
            "paradigm_labels": [
                {
                    "label": "conceptual_theory",
                    "confidence": 0.7,
                    "primary": True,
                    "evidence_refs": [{"page": 1, "start_line": 1, "end_line": 2, "quote": "Model setup text"}],
                },
                {
                    "label": "simulation_calibration",
                    "confidence": 0.6,
                    "primary": False,
                    "evidence_refs": [{"page": 2, "start_line": 1, "end_line": 2, "quote": "Results text"}],
                },
            ],
            "rationale": "mixed methods",
        }
        result = parse_classification(payload, _sample_page_index())
        assert result.coarse_family == "mixed"
        assert len(result.paradigm_labels) == 2


class TestClassifyManuscript:
    def test_theoretical(self, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(FIXTURES.parent / "review_outputs_test_classify"))
        provider = MockProvider("test", type("Profile", (), {"type": "mock", "model": "mock", "vendor": None})())
        result = classify_manuscript(
            provider=provider,
            indexed_text="We develop a formal model with propositions and proofs. This is a theoretical paper.",
            page_index=_sample_page_index(),
            criteria=_sample_criteria(),
        )
        assert result is not FALLBACK_PARADIGM
        assert result.coarse_family == "theoretical"
        assert any(lb.label == "formal_modeling" for lb in result.paradigm_labels)

    def test_empirical(self):
        provider = MockProvider("test", type("Profile", (), {"type": "mock", "model": "mock", "vendor": None})())
        result = classify_manuscript(
            provider=provider,
            indexed_text="We conduct a randomized controlled trial with 2400 participants across treatment and control groups. Statistical analysis reveals significant effects.",
            page_index=_sample_page_index(),
            criteria=_sample_criteria(),
        )
        assert result is not FALLBACK_PARADIGM
        assert result.coarse_family == "empirical"
        assert any(lb.label == "experiment" for lb in result.paradigm_labels)

    def test_fallback_on_provider_error(self):
        class FailingProvider(MockProvider):
            def generate(self, *, prompt: str):
                raise RuntimeError("simulated failure")

        provider = FailingProvider("test", type("Profile", (), {"type": "mock", "model": "mock", "vendor": None})())
        result = classify_manuscript(
            provider=provider,
            indexed_text="some text",
            page_index=_sample_page_index(),
            criteria=_sample_criteria(),
        )
        assert result is FALLBACK_PARADIGM


class TestTaxonomyTable:
    def test_builds_table_from_criteria(self):
        criteria = _sample_criteria()
        table = _build_taxonomy_table(criteria)
        assert "formal_modeling" in table
        assert "experiment" in table
        assert "theoretical" in table.lower() or "theoretical" in table
