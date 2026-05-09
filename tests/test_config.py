from fusion_reviewer.config import get_settings, load_paradigm_criteria, load_roles


def test_settings_load():
    settings = get_settings()
    assert settings.data_dir is not None
    assert settings.max_evidence_chars > 0


def test_paradigm_criteria_loads():
    criteria = load_paradigm_criteria()
    assert hasattr(criteria, 'paradigms')
    assert hasattr(criteria, 'fallback_focus')


def test_roles_loads():
    roles = load_roles()
    assert hasattr(roles, 'generalists')
