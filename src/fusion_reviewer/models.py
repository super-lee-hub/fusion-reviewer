from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from deepreview.types import JobArtifacts, JobStatus, UsageSnapshot
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


Severity = Literal["low", "medium", "high", "critical"]
ConsensusState = Literal["consensus", "disagreement", "single-source"]
DecisionValue = Literal["accept", "minor_revision", "major_revision", "reject"]
DocumentType = Literal["pdf", "docx", "doc"]
RuntimeMode = Literal["backend", "codex"]
LayoutFidelity = Literal["full", "degraded", "text_only"]
ReviewSource = Literal["subagent", "local", "service", "unknown"]
CoarseFamily = Literal["empirical", "theoretical", "mixed", "review_synthesis"]


class ParadigmLabel(BaseModel):
    label: str
    confidence: float = 1.0
    primary: bool = False
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class ManuscriptParadigm(BaseModel):
    coarse_family: CoarseFamily | str = "mixed"
    paradigm_labels: list[ParadigmLabel] = Field(default_factory=list)
    rationale: str = ""


def _make_fallback_paradigm() -> ManuscriptParadigm:
    return ManuscriptParadigm(
        coarse_family="unknown",
        paradigm_labels=[],
        rationale="Classification failed — reviewers instructed to apply criteria appropriate to the paper's apparent methodology.",
    )


FALLBACK_PARADIGM: ManuscriptParadigm


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

    @model_validator(mode="after")
    def _fill_issue_key(self) -> "Finding":
        if not self.issue_key:
            self.issue_key = self.id
        return self


class AgentReview(BaseModel):
    agent_id: str
    kind: Literal["generalist", "specialist", "editor"]
    title: str
    provider_profile: str
    model: str
    review_source: ReviewSource = "unknown"
    status: Literal["completed", "failed"] = "completed"
    summary: str = ""
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    recommendation: DecisionValue = "major_revision"
    findings: list[Finding] = Field(default_factory=list)
    markdown: str = ""
    error: str | None = None
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
    provider_profile: str = "mock_local"
    model: str = "mock-editor-v1"
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


class AgentSummary(BaseModel):
    agent_id: str
    kind: str
    title: str
    status: str
    category: str | None = None
    artifact_markdown: str | None = None
    artifact_json: str | None = None


class FusionJobState(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID = Field(default_factory=uuid4)
    title: str
    source_name: str | None = None
    source_pdf_name: str | None = None
    run_label: str | None = None
    document_type: DocumentType = "pdf"
    mode: RuntimeMode = "backend"
    status: JobStatus = JobStatus.queued
    message: str = "Job queued."
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    usage: UsageSnapshot = Field(default_factory=UsageSnapshot)
    annotation_count: int = 0
    final_report_ready: bool = False
    pdf_ready: bool = False
    artifacts: JobArtifacts = Field(default_factory=JobArtifacts)
    metadata: dict[str, Any] = Field(default_factory=dict)
    agents: list[AgentSummary] = Field(default_factory=list)
    concerns_count: int = 0
    decision: DecisionValue | None = None
    provider_override: str | None = None
    normalized_source_path: str | None = None
    layout_fidelity: LayoutFidelity | None = None
    extractor_used: str | None = None
    conversion_used: str | None = None
    journal_context_present: bool = False
    journal_context_source: str | None = None
    mineru_attempted: bool | None = None
    mineru_succeeded: bool | None = None
    manuscript_paradigm: ManuscriptParadigm | None = None
    revision_context_present: bool = False
    revision_context_source: str | None = None
    revision_extraction_quality: str | None = None

    @model_validator(mode="after")
    def _fill_source_name(self) -> "FusionJobState":
        if not self.source_name and self.source_pdf_name:
            self.source_name = self.source_pdf_name
        if not self.source_pdf_name and self.source_name:
            self.source_pdf_name = self.source_name
        return self


class JobResult(BaseModel):
    job: FusionJobState
    reviews: list[AgentReview] = Field(default_factory=list)
    concerns: list[Concern] = Field(default_factory=list)
    editor: EditorReport | None = None


ManuscriptParadigm.model_rebuild()
FALLBACK_PARADIGM = _make_fallback_paradigm()
