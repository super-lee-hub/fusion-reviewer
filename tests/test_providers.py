from __future__ import annotations

from fusion_reviewer.providers import MockProvider


def _mock_profile():
    from fusion_reviewer.config import ProviderProfile
    return ProviderProfile(type="mock", model="mock-model", vendor=None)


def _provider():
    return MockProvider("test", _mock_profile())


class TestMockProviderGenerate:
    def test_theoretical_keyword_returns_theoretical(self):
        p = _provider()
        result = p.generate(prompt="Classify this manuscript. theoretical approach with formal model.")
        assert result.payload["coarse_family"] == "theoretical"
        labels = result.payload["paradigm_labels"]
        assert any(lb["label"] == "formal_modeling" for lb in labels)

    def test_empirical_keyword_returns_empirical(self):
        p = _provider()
        result = p.generate(prompt="Classify this manuscript. empirical experiment with data.")
        assert result.payload["coarse_family"] == "empirical"
        labels = result.payload["paradigm_labels"]
        assert any(lb["label"] == "experiment" for lb in labels)

    def test_classify_overrides_takes_priority(self):
        p = _provider()
        MockProvider._classify_overrides["custom_key_123"] = {
            "coarse_family": "review_synthesis",
            "paradigm_labels": [
                {
                    "label": "meta_analysis",
                    "confidence": 0.95,
                    "primary": True,
                    "evidence_refs": [{"page": 1, "start_line": 1, "end_line": 2, "quote": "custom"}],
                }
            ],
            "rationale": "custom override",
        }
        try:
            result = p.generate(prompt="Classify with custom_key_123 present in prompt.")
            assert result.payload["coarse_family"] == "review_synthesis"
            assert any(lb["label"] == "meta_analysis" for lb in result.payload["paradigm_labels"])
        finally:
            MockProvider._classify_overrides.pop("custom_key_123", None)
