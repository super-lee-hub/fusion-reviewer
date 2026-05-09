from fusion_reviewer.concern_merge import SEVERITY_ORDER, canonical_issue_key, merge_concerns, slugify
from fusion_reviewer.models import AgentReview, Finding


def _make_review(agent_id: str, kind: str = "generalist", findings: list[Finding] | None = None) -> AgentReview:
    return AgentReview(
        agent_id=agent_id,
        kind=kind,
        title=f"Reviewer {agent_id}",
        findings=findings or [],
        status="completed",
    )


def _make_finding(
    issue_key: str,
    title: str,
    category: str = "method",
    severity: str = "medium",
    evidence_refs: list | None = None,
) -> Finding:
    from fusion_reviewer.models import EvidenceRef
    return Finding(
        id=issue_key,
        issue_key=issue_key,
        title=title,
        description=f"Description of {title}",
        category=category,
        severity=severity,
        evidence_refs=evidence_refs or [EvidenceRef(page=1, start_line=1, end_line=2, quote="test")],
    )


def test_slugify():
    assert slugify("Test Issue Key") == "test_issue_key"
    assert slugify("method_identification_weakness") == "method_identification_weakness"
    assert slugify("") == ""


def test_canonical_issue_key():
    result = canonical_issue_key("my_key", "Some Title", "method")
    assert result == "my_key"

    result2 = canonical_issue_key(None, "Weak Evidence", "method")
    assert "weak_evidence" in result2


def test_merge_concerns_single_review():
    f1 = _make_finding("method_weak", "Method Weakness")
    review = _make_review("agent_a", findings=[f1])
    concerns = merge_concerns([review])
    assert len(concerns) == 1
    assert concerns[0].issue_key == "method_weak"
    assert concerns[0].consensus_state == "single-source"


def test_merge_concerns_consensus():
    f1 = _make_finding("shared_issue", "Shared Issue")
    r1 = _make_review("agent_a", findings=[f1])
    r2 = _make_review("agent_b", findings=[f1])
    concerns = merge_concerns([r1, r2])
    assert len(concerns) == 1
    assert concerns[0].consensus_state == "consensus"
    assert len(concerns[0].raised_by) == 2


def test_merge_concerns_skips_failed():
    f1 = _make_finding("valid", "Valid Finding")
    r1 = _make_review("agent_a", findings=[f1])
    r1.status = "failed"
    concerns = merge_concerns([r1])
    assert len(concerns) == 0


def test_merge_concerns_severity_ordering():
    f_low = _make_finding("low_issue", "Low", severity="low")
    f_high = _make_finding("high_issue", "High", severity="high")
    r1 = _make_review("agent_a", findings=[f_low, f_high])
    concerns = merge_concerns([r1])
    assert concerns[0].severity == "high"


def test_severity_order():
    assert SEVERITY_ORDER["low"] < SEVERITY_ORDER["high"]
    assert SEVERITY_ORDER["critical"] > SEVERITY_ORDER["medium"]
