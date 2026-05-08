from fusion_reviewer.config import load_provider_profiles, load_review_plan
from fusion_reviewer.providers import ProviderRegistry


def test_provider_profiles_load():
    profiles = load_provider_profiles()
    assert "mock_local" in profiles
    assert profiles["mock_local"].enabled is True


def test_review_plan_contains_expected_slots():
    plan = load_review_plan()
    assert len(plan.generalists) == 3
    assert len(plan.specialists) == 5
    assert plan.editor.id == "meta_editor"


def test_openai_compatible_health_messages(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AIHUBMIX_API_KEY", raising=False)
    monkeypatch.delenv("AIHUBMIX_BASE_URL", raising=False)

    registry = ProviderRegistry()

    openai_health = registry.build("openai_default").health()
    aihubmix_health = registry.build("aihubmix").health()

    assert openai_health["ok"] is False
    assert openai_health["message"] == "missing api key"
    assert aihubmix_health["ok"] is False
    assert aihubmix_health["message"] == "missing api key and base url"
