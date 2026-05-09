"""Verify SKILL.md frontmatter contains all required trigger terms."""

from pathlib import Path

SKILL_MD = Path(__file__).resolve().parents[1] / "skills" / "paper-review-committee" / "SKILL.md"

REQUIRED_TERMS = [
    "paper review",
    "PDF",
    "DOCX",
    "DOC",
    "committee review",
    "journal fit",
    "revision responses",
    "evidence bundles",
    "Codex",
    "Claude Code",
]


def test_skill_md_exists():
    assert SKILL_MD.exists(), f"SKILL.md not found at {SKILL_MD}"


def test_skill_md_frontmatter_has_trigger_terms():
    content = SKILL_MD.read_text(encoding="utf-8")
    # Frontmatter is between first and second ---
    parts = content.split("---", 2)
    assert len(parts) >= 2, "SKILL.md must have YAML frontmatter (--- delimiters)"
    frontmatter = parts[1]
    for term in REQUIRED_TERMS:
        assert term.lower() in frontmatter.lower(), f"Missing trigger term in frontmatter: '{term}'"


def test_skill_md_has_no_backend_references():
    content = SKILL_MD.read_text(encoding="utf-8")
    assert "providers.yaml" not in content, "SKILL.md should not reference providers.yaml"
    assert "review_plan.yaml" not in content, "SKILL.md should not reference review_plan.yaml"


def test_skill_md_has_provenance_section():
    content = SKILL_MD.read_text(encoding="utf-8")
    assert "Provenance Taxonomy" in content, "SKILL.md should have Provenance Taxonomy section"


def test_skill_md_has_committee_modes():
    content = SKILL_MD.read_text(encoding="utf-8")
    assert "draft_only" in content, "SKILL.md should document draft_only mode"
    assert "partial" in content, "SKILL.md should document partial mode"
    assert "full" in content, "SKILL.md should document full mode"
