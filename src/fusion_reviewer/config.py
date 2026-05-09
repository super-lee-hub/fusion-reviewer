from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_REFERENCES = PROJECT_ROOT / "skills" / "paper-review-committee" / "references"
load_dotenv(PROJECT_ROOT / ".env", override=False)


class FusionSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    data_dir: Path = Field(default=PROJECT_ROOT / "review_outputs", alias="DATA_DIR")

    # MinerU settings
    mineru_base_url: str = Field(default="https://mineru.net/api/v4", alias="MINERU_BASE_URL")
    mineru_api_token: str | None = Field(default=None, alias="MINERU_API_TOKEN")
    mineru_model_version: str = Field(default="vlm", alias="MINERU_MODEL_VERSION")
    mineru_poll_interval_seconds: float = Field(default=3.0, alias="MINERU_POLL_INTERVAL_SECONDS")
    mineru_poll_timeout_seconds: int = Field(default=900, alias="MINERU_POLL_TIMEOUT_SECONDS")
    mineru_request_max_retries: int = Field(default=2, alias="MINERU_REQUEST_MAX_RETRIES")
    mineru_retry_backoff_seconds: float = Field(default=1.5, alias="MINERU_RETRY_BACKOFF_SECONDS")
    allow_local_parse_fallback: bool = Field(default=True, alias="ALLOW_LOCAL_PARSE_FALLBACK")

    # Document processing
    max_evidence_chars: int = Field(default=120000, alias="MAX_EVIDENCE_CHARS")
    force_english_output: bool = Field(default=False, alias="FORCE_ENGLISH_OUTPUT")
    preprocess_cache_dirname: str = Field(default="_normalize_cache", alias="PREPROCESS_CACHE_DIRNAME")
    enable_page_snapshots: bool = Field(default=True, alias="ENABLE_PAGE_SNAPSHOTS")
    max_snapshot_pages: int = Field(default=8, alias="MAX_SNAPSHOT_PAGES")
    libreoffice_bin: str | None = Field(default=None, alias="LIBREOFFICE_BIN")

    # Classification
    paradigm_criteria_file: Path = Field(default=PROJECT_ROOT / "skills" / "paper-review-committee" / "references" / "paradigm_criteria.yaml", alias="PARADIGM_CRITERIA_FILE")
    roles_file: Path = Field(default=PROJECT_ROOT / "skills" / "paper-review-committee" / "references" / "roles.yaml", alias="ROLES_FILE")
    classifier_confidence_accept: float = Field(default=0.6, alias="CLASSIFIER_CONFIDENCE_ACCEPT")
    classifier_confidence_reject: float = Field(default=0.4, alias="CLASSIFIER_CONFIDENCE_REJECT")

    # Optional: deepreview root for PDF export (best-effort)
    deepreview_root: Path = Field(default=PROJECT_ROOT.parent / "DeepReviewer-v2", alias="DEEPREVIEW_ROOT")
    attach_source_pdf_appendix: bool = Field(default=False, alias="ATTACH_SOURCE_PDF_APPENDIX")


class RoleSlotConfig(BaseModel):
    id: str
    kind: str
    title: str
    category: str | None = None
    tone_instruction: str = ""
    focus_areas: list[str] = Field(default_factory=list)


class RoleSetConfig(BaseModel):
    generalists: list[RoleSlotConfig] = Field(default_factory=list)
    specialists: list[RoleSlotConfig] = Field(default_factory=list)
    editor: RoleSlotConfig | None = None


class ParadigmCriteriaItem(BaseModel):
    tag: str
    coarse_family: str
    appropriate_focus: list[str] = Field(default_factory=list)
    inappropriate_critique_patterns: list[str] = Field(default_factory=list)


class ParadigmCriteriaConfig(BaseModel):
    paradigms: list[ParadigmCriteriaItem] = Field(default_factory=list)
    fallback_focus: list[str] = Field(default_factory=list)


@lru_cache(maxsize=1)
def get_settings() -> FusionSettings:
    settings = FusionSettings()
    settings.data_dir = (settings.data_dir if settings.data_dir.is_absolute() else (PROJECT_ROOT / settings.data_dir).resolve())
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings


@lru_cache(maxsize=1)
def load_roles() -> RoleSetConfig:
    settings = get_settings()
    if not settings.roles_file.exists():
        return RoleSetConfig()
    payload = yaml.safe_load(settings.roles_file.read_text(encoding="utf-8")) or {}
    return RoleSetConfig.model_validate(payload)


@lru_cache(maxsize=1)
def load_paradigm_criteria() -> ParadigmCriteriaConfig:
    settings = get_settings()
    if not settings.paradigm_criteria_file.exists():
        return ParadigmCriteriaConfig()
    payload = yaml.safe_load(settings.paradigm_criteria_file.read_text(encoding="utf-8")) or {}
    return ParadigmCriteriaConfig.model_validate(payload)
