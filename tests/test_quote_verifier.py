from fusion_reviewer.quote_verifier import (
    _normalize_text,
    validate_revision_assessment_evidence,
    verify_quote,
)
from fusion_reviewer.models import EvidenceRef


def _make_page_index():
    return {
        1: ["This is the first page of the manuscript."],
        2: ["Methods section: We conducted an experiment.", "Participants were recruited online."],
        3: ["Results show significant effects of treatment."],
    }


def test_verify_quote_exact():
    pi = _make_page_index()
    result = verify_quote("We conducted an experiment.", pi, page=2, start_line=1, end_line=1)
    assert result == "exact"


def test_verify_quote_normalized_whitespace():
    pi = _make_page_index()
    result = verify_quote("We  conducted  an  experiment.", pi, page=2, start_line=1, end_line=1)
    assert result == "normalized"


def test_verify_quote_not_found():
    pi = _make_page_index()
    # Quote not matching any page, but page reference exists and is valid
    result = verify_quote("This text does not exist anywhere.", pi, page=1, start_line=1, end_line=1)
    assert result == "page_line_only"  # page exists, quote text not found on that page

    # No page reference at all + not found = not_found
    result2 = verify_quote("This text does not exist anywhere.", pi, page=None, start_line=None, end_line=None)
    assert result2 == "not_found"


def test_verify_quote_page_line_only():
    pi = _make_page_index()
    result = verify_quote("nonexistent quote text", pi, page=2, start_line=1, end_line=1)
    assert result == "page_line_only"


def test_verify_quote_empty():
    pi = _make_page_index()
    result = verify_quote("", pi, page=1, start_line=1, end_line=1)
    assert result == "not_found"


def test_normalize_text_collapses_whitespace():
    result = _normalize_text("hello   world\n\nfoo  bar")
    assert "  " not in result
    assert "\n" not in result


def test_validate_revision_assessment_downgrade():
    valid, downgraded = validate_revision_assessment_evidence("addressed", [])
    assert not valid
    assert downgraded == "partially_addressed"


def test_validate_revision_assessment_ok():
    ref = EvidenceRef(page=1, start_line=1, end_line=2, quote="evidence text")
    valid, downgraded = validate_revision_assessment_evidence("addressed", [ref])
    assert valid
    assert downgraded == ""


def test_validate_revision_assessment_other_status():
    valid, _ = validate_revision_assessment_evidence("unclear", [])
    assert valid
