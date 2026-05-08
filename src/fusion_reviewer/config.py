from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DEEPREVIEW_ROOT = PROJECT_ROOT.parent / "DeepReviewer-v2"
load_dotenv(PROJECT_ROOT / ".env", override=False)


class FusionSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    data_dir: Path = Field(default=PROJECT_ROOT / "review_outputs", alias="DATA_DIR")
    deepreview_root: Path = Field(default=DEFAULT_DEEPREVIEW_ROOT, alias="DEEPREVIEW_ROOT")
    providers_file: Path = Field(default=PROJECT_ROOT / "providers.yaml", alias="PROVIDERS_FILE")
    review_plan_file: Path = Field(default=PROJECT_ROOT / "review_plan.yaml", alias="REVIEW_PLAN_FILE")
    roles_file: Path = Field(default=PROJECT_ROOT / "roles.yaml", alias="ROLES_FILE")

    mineru_base_url: str = Field(default="https://mineru.net/api/v4", alias="MINERU_BASE_URL")
    mineru_api_token: str | None = Field(default=None, alias="MINERU_API_TOKEN")
    mineru_model_version: str = Field(default="vlm", alias="MINERU_MODEL_VERSION")
    mineru_upload_endpoint: str = Field(default="/file-urls/batch", alias="MINERU_UPLOAD_ENDPOINT")
    mineru_poll_endpoint_templates: str = Field(
        default="/extract-results/batch/{batch_id},/extract-results/{batch_id},/extract/task/{batch_id}",
        alias="MINERU_POLL_ENDPOINT_TEMPLATES",
    )
    mineru_poll_interval_seconds: float = Field(default=3.0, alias="MINERU_POLL_INTERVAL_SECONDS")
    mineru_poll_timeout_seconds: int = Field(default=900, alias="MINERU_POLL_TIMEOUT_SECONDS")
    mineru_request_max_retries: int = Field(default=2, alias="MINERU_REQUEST_MAX_RETRIES")
    mineru_retry_backoff_seconds: float = Field(default=1.5, alias="MINERU_RETRY_BACKOFF_SECONDS")
    allow_local_parse_fallback: bool = Field(default=True, alias="ALLOW_LOCAL_PARSE_FALLBACK")

    paper_search_enabled: bool = Field(default=False, alias="PAPER_SEARCH_ENABLED")
    paper_search_base_url: str | None = Field(default=None, alias="PAPER_SEARCH_BASE_URL")
    paper_search_api_key: str | None = Field(default=None, alias="PAPER_SEARCH_API_KEY")
    paper_search_endpoint: str = Field(default="/pasa/search", alias="PAPER_SEARCH_ENDPOINT")
    paper_search_health_endpoint: str = Field(default="/health", alias="PAPER_SEARCH_HEALTH_ENDPOINT")

    default_wait_seconds: int = Field(default=8, alias="DEFAULT_WAIT_SECONDS")
    max_evidence_chars: int = Field(default=120000, alias="MAX_EVIDENCE_CHARS")
    force_english_output: bool = Field(default=False, alias="FORCE_ENGLISH_OUTPUT")
    attach_source_pdf_appendix: bool = Field(default=False, alias="ATTACH_SOURCE_PDF_APPENDIX")
    web_host: str = Field(default="127.0.0.1", alias="WEB_HOST")
    web_port: int = Field(default=8123, alias="WEB_PORT")
    preprocess_cache_dirname: str = Field(default="_normalize_cache", alias="PREPROCESS_CACHE_DIRNAME")
    enable_page_snapshots: bool = Field(default=True, alias="ENABLE_PAGE_SNAPSHOTS")
    max_snapshot_pages: int = Field(default=8, alias="MAX_SNAPSHOT_PAGES")
    libreoffice_bin: str | None = Field(default=None, alias="LIBREOFFICE_BIN")
    paradigm_criteria_file: Path = Field(default=PROJECT_ROOT / "paradigm_criteria.yaml", alias="PARADIGM_CRITERIA_FILE")
    classifier_confidence_accept: float = Field(default=0.6, alias="CLASSIFIER_CONFIDENCE_ACCEPT")
    classifier_confidence_reject: float = Field(default=0.4, alias="CLASSIFIER_CONFIDENCE_REJECT")

    def mineru_poll_templates(self) -> list[str]:
        return [item.strip() for item in self.mineru_poll_endpoint_templates.split(",") if item.strip()]


class ProviderProfile(BaseModel):
    type: str
    model: str
    base_url: str | None = None
    api_key_env: str | None = None
    supports_tools: bool = False
    enabled: bool = True
    vendor: str | None = None
    timeout_seconds: int = 120
    extra_headers: dict[str, str] = Field(default_factory=dict)


class RoleSlotConfig(BaseModel):
    id: str
    kind: str
    title: str
    category: str | None = None
    tone_instruction: str
    focus_areas: list[str] = Field(default_factory=list)


class RoleSetConfig(BaseModel):
    generalists: list[RoleSlotConfig]
    specialists: list[RoleSlotConfig]
    editor: RoleSlotConfig


class RuntimeSlotConfig(BaseModel):
    id: str
    profile: str
    model: str | None = None


class RuntimePlanConfig(BaseModel):
    default_mode: str = "backend"
    generalists: list[RuntimeSlotConfig]
    specialists: list[RuntimeSlotConfig]
    editor: RuntimeSlotConfig


class AgentSlotConfig(BaseModel):
    id: str
    kind: str
    title: str
    profile: str
    model: str | None = None
    category: str | None = None
    tone_instruction: str
    focus_areas: list[str] = Field(default_factory=list)


class ReviewPlanConfig(BaseModel):
    generalists: list[AgentSlotConfig]
    specialists: list[AgentSlotConfig]
    editor: AgentSlotConfig


class ParadigmCriteriaItem(BaseModel):
    tag: str
    coarse_family: str
    appropriate_focus: list[str] = Field(default_factory=list)
    inappropriate_critique_patterns: list[str] = Field(default_factory=list)


class ParadigmCriteriaConfig(BaseModel):
    paradigms: list[ParadigmCriteriaItem] = Field(default_factory=list)
    fallback_focus: list[str] = Field(default_factory=list)


_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _resolve_env_placeholders(value: Any) -> Any:
    if isinstance(value, str):
        def repl(match: re.Match[str]) -> str:
            return os.getenv(match.group(1), "")

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {key: _resolve_env_placeholders(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_env_placeholders(item) for item in value]
    return value


def _resolve_project_path(path: Path) -> Path:
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _merge_slot(role: RoleSlotConfig, runtime: RuntimeSlotConfig) -> AgentSlotConfig:
    if role.id != runtime.id:
        raise ValueError(f"Role/runtime mismatch: {role.id} != {runtime.id}")
    return AgentSlotConfig(
        id=role.id,
        kind=role.kind,
        title=role.title,
        profile=runtime.profile,
        model=runtime.model,
        category=role.category,
        tone_instruction=role.tone_instruction,
        focus_areas=list(role.focus_areas),
    )


def _merge_slot_group(
    *,
    role_group: list[RoleSlotConfig],
    runtime_group: list[RuntimeSlotConfig],
    group_name: str,
) -> list[AgentSlotConfig]:
    role_map = {slot.id: slot for slot in role_group}
    runtime_ids = [slot.id for slot in runtime_group]
    role_ids = [slot.id for slot in role_group]
    if role_ids != runtime_ids:
        raise ValueError(
            f"{group_name} ids must match exactly between roles.yaml and review_plan.yaml; "
            f"roles={role_ids}, runtime={runtime_ids}"
        )
    return [_merge_slot(role_map[slot.id], slot) for slot in runtime_group]


@lru_cache(maxsize=1)
def get_settings() -> FusionSettings:
    settings = FusionSettings()
    settings.data_dir = _resolve_project_path(settings.data_dir)
    settings.deepreview_root = _resolve_project_path(settings.deepreview_root)
    settings.providers_file = _resolve_project_path(settings.providers_file)
    settings.review_plan_file = _resolve_project_path(settings.review_plan_file)
    settings.roles_file = _resolve_project_path(settings.roles_file)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / ".job_index").mkdir(parents=True, exist_ok=True)
    return settings


@lru_cache(maxsize=1)
def load_provider_profiles() -> dict[str, ProviderProfile]:
    settings = get_settings()
    payload = yaml.safe_load(settings.providers_file.read_text(encoding="utf-8")) or {}
    profiles = payload.get("profiles", {})
    resolved = _resolve_env_placeholders(profiles)
    return {name: ProviderProfile.model_validate(raw) for name, raw in resolved.items()}


@lru_cache(maxsize=1)
def load_roles() -> RoleSetConfig:
    settings = get_settings()
    payload = yaml.safe_load(settings.roles_file.read_text(encoding="utf-8")) or {}
    return RoleSetConfig.model_validate(payload)


@lru_cache(maxsize=1)
def load_runtime_plan() -> RuntimePlanConfig:
    settings = get_settings()
    payload = yaml.safe_load(settings.review_plan_file.read_text(encoding="utf-8")) or {}
    return RuntimePlanConfig.model_validate(payload)


@lru_cache(maxsize=1)
def load_review_plan() -> ReviewPlanConfig:
    roles = load_roles()
    runtime = load_runtime_plan()
    return ReviewPlanConfig(
        generalists=_merge_slot_group(
            role_group=roles.generalists,
            runtime_group=runtime.generalists,
            group_name="generalists",
        ),
        specialists=_merge_slot_group(
            role_group=roles.specialists,
            runtime_group=runtime.specialists,
            group_name="specialists",
        ),
        editor=_merge_slot(roles.editor, runtime.editor),
    )


@lru_cache(maxsize=1)
def load_paradigm_criteria() -> ParadigmCriteriaConfig:
    settings = get_settings()
    path = _resolve_project_path(settings.paradigm_criteria_file)
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return ParadigmCriteriaConfig.model_validate(payload)
