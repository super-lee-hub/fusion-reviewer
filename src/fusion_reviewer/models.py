from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---- local types (replaces deepreview.types imports) -------------------------

class JobStatus(str, Enum):
    queued = "queued"
    pdf_parsing = "pdf_parsing"
    agent_running = "agent_running"
    final_report_persisting = "final_report_persisting"
    pdf_exporting = "pdf_exporting"
    completed = "completed"
    failed = "failed"


class UsageSnapshot(BaseModel):
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


# ---- shared literals ---------------------------------------------------------

Severity = Literal["low", "medium", "high", "critical"]
ConsensusState = Literal["consensus", "disagreement", "single-source"]
DecisionValue = Literal["accept", "minor_revision", "major_revision", "reject"]
DocumentType = Literal["pdf", "docx", "doc"]
LayoutFidelity = Literal["full", "degraded", "text_only"]
ReviewSource = Literal["subagent", "serial_local", "local", "unknown"]
AgentHost = Literal["codex", "claude_code", "other", "unknown"]
CoarseFamily = Literal["empirical", "theoretical", "mixed", "review_synthesis"]
CommitteeMode = Literal["draft_only", "partial", "full"]
EditorMode = Literal["present", "draft_only", "absent"]
RevisionMode = Literal["none", "reviewer_assessments_only", "full_synthesis"]
RevisionStatus = Literal["addressed", "partially_addressed", "not_addressed", "unclear"]
FindingOrigin = Literal["original", "new_after_revision"]

ARTIFACT_CONTRACT_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0.0"


# ---- evidence / findings -----------------------------------------------------

class EvidenceRef(BaseModel):
    page: int | None = None
    start_line: int | None = None
    end_line: int | None = None
    quote: str = ""
    locator: str | None = None
    image_path: str | None = None


class Finding(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    issue_key: str | None = None
    title: str
    description: str
    category: str
    severity: Severity = "medium"
    evidence_refs: list[EvidenceRef] = Field(
        default_factory=list,
        validation_alias=AliasChoices("evidence_refs", "evidence_spans"),
    )
    needs_external_verification: bool = False
    recommendation: str | None = None
    origin: FindingOrigin = "original"

    @model_validator(mode="after")
    def _fill_issue_key(self) -> "Finding":
        if not self.issue_key:
            self.issue_key = self.id
        return self


# ---- paradigm ----------------------------------------------------------------

class ParadigmLabel(BaseModel):
    label: str
    confidence: float = 1.0
    primary: bool = False
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class ManuscriptParadigm(BaseModel):
    coarse_family: CoarseFamily | str = "mixed"
    paradigm_labels: list[ParadigmLabel] = Field(default_factory=list)
    rationale: str = ""
    schema_version: str = Field(default=SCHEMA_VERSION)


def _make_fallback_paradigm() -> ManuscriptParadigm:
    return ManuscriptParadigm(
        coarse_family="unknown",
        paradigm_labels=[],
        rationale="Classification failed — reviewers instructed to apply criteria appropriate to the paper's apparent methodology.",
    )


FALLBACK_PARADIGM: ManuscriptParadigm


# ---- reviews -----------------------------------------------------------------

class AgentReview(BaseModel):
    agent_id: str
    kind: Literal["generalist", "specialist", "editor"]
    title: str
    provider_profile: str = ""
    model: str = ""
    review_source: ReviewSource = "unknown"
    agent_host: AgentHost = "unknown"
    status: Literal["completed", "failed"] = "completed"
    summary: str = ""
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    recommendation: DecisionValue = "major_revision"
    findings: list[Finding] = Field(default_factory=list)
    markdown: str = ""
    error: str | None = None
    schema_version: str = Field(default=SCHEMA_VERSION)
    created_at: datetime = Field(default_factory=utcnow)


class Concern(BaseModel):
    id: str
    issue_key: str
    title: str
    description: str
    category: str
    severity: Severity = "medium"
    evidence_refs: list[EvidenceRef] = Field(
        default_factory=list,
        validation_alias=AliasChoices("evidence_refs", "evidence_spans"),
    )
    raised_by: list[str] = Field(default_factory=list)
    specialist_flags: list[str] = Field(default_factory=list)
    needs_external_verification: bool = False
    consensus_state: ConsensusState = "single-source"


class EditorReport(BaseModel):
    agent_id: str = "meta_editor"
    title: str = "Meta Review Editor"
    provider_profile: str = ""
    model: str = ""
    decision: DecisionValue = "major_revision"
    expected_subagent_reviews: int | None = None
    completed_subagent_reviews: int = 0
    completed_local_reviews: int = 0
    completed_service_reviews: int = 0
    completed_unknown_source_reviews: int = 0
    missing_subagent_slots: int = 0
    full_subagent_committee: bool | None = None
    consensus: list[str] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    priority_revisions: list[str] = Field(default_factory=list)
    decision_rationale: str = ""
    markdown: str = ""
    status: Literal["completed", "failed"] = "completed"
    error: str | None = None
    schema_version: str = Field(default=SCHEMA_VERSION)


class AgentSummary(BaseModel):
    agent_id: str
    kind: str
    title: str
    status: str
    category: str | None = None
    artifact_markdown: str | None = None
    artifact_json: str | None = None


# ---- revision dual-track -----------------------------------------------------

class PreviousConcern(BaseModel):
    """A concern from the previous review round."""
    id: str
    issue_key: str
    title: str
    description: str = ""
    severity: Severity = "medium"
    status_from_previous: str | None = None


class RevisionClaim(BaseModel):
    """Author's claimed response to a previous concern."""
    concern_id: str
    claimed_change: str
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class RevisionAssessment(BaseModel):
    """Per-concern assessment of revision quality."""
    concern_id: str
    status: RevisionStatus = "unclear"
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    rationale: str = ""
    schema_version: str = Field(default=SCHEMA_VERSION)


class RevisionResponseReview(BaseModel):
    """Aggregate revision response review (host-agent-produced)."""
    assessments: list[RevisionAssessment] = Field(default_factory=list)
    summary: str = ""
    quality_assessment: str = ""
    markdown: str = ""
    schema_version: str = Field(default=SCHEMA_VERSION)


# ---- final summary -----------------------------------------------------------

class FinalSummary(BaseModel):
    run_id: str = ""
    title: str = ""
    source_name: str = ""
    decision: DecisionValue | None = None
    concerns_count: int = 0
    completed_reviews: int = 0
    failed_reviews: int = 0
    committee_mode: CommitteeMode = "draft_only"
    editor_mode: EditorMode = "absent"
    revision_mode: RevisionMode | None = None
    editor_synthesis_present: bool = False
    revision_response_present: bool = False
    pdf_generated: bool | None = None
    mineru_used: bool | None = None
    libreoffice_used: bool | None = None
    provenance_summary: dict[str, int] = Field(default_factory=dict)
    artifact_contract_version: str = Field(default=ARTIFACT_CONTRACT_VERSION)
    schema_version: str = Field(default=SCHEMA_VERSION)
    layout_fidelity: str | None = None
    extractor_used: str | None = None
    conversion_used: str | None = None
    journal_context_present: bool = False
    journal_context_source: str | None = None
    mineru_attempted: bool | None = None
    mineru_succeeded: bool | None = None
    revision_context_present: bool = False
    revision_context_source: str | None = None
    revision_extraction_quality: str | None = None
    reviewers: list[dict[str, Any]] = Field(default_factory=list)


# ---- model rebuild -----------------------------------------------------------

ManuscriptParadigm.model_rebuild()
FALLBACK_PARADIGM = _make_fallback_paradigm()
