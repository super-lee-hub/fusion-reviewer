"""Provenance taxonomy and review source inference — no LLM/API calls."""

from __future__ import annotations

from typing import Literal

from .concern_merge import slugify
from .models import AgentReview

ReviewSource = Literal["subagent", "serial_local", "local", "unknown"]
AgentHost = Literal["codex", "claude_code", "other", "unknown"]

REVIEW_SOURCE_VALUES: tuple[str, ...] = ("subagent", "serial_local", "local", "unknown")
AGENT_HOST_VALUES: tuple[str, ...] = ("codex", "claude_code", "other", "unknown")


def map_legacy_provenance(
    review_source: str | None,
    provider_profile: str | None = None,
    model: str | None = None,
) -> dict[str, str | None]:
    """Map legacy provenance fields to new taxonomy.

    Old ``service`` maps to ``unknown``. Old ``provider_profile`` and
    ``model`` are returned as metadata only.
    """
    mapped_source: str
    if review_source in ("subagent", "serial_local", "local"):
        mapped_source = review_source
    elif review_source == "service":
        mapped_source = "unknown"
    elif review_source and review_source.strip():
        mapped_source = "unknown"
    else:
        mapped_source = "unknown"
    return {
        "review_source": mapped_source,
        "agent_host": "unknown",
        "_legacy_provider_profile": provider_profile or None,
        "_legacy_model": model or None,
    }


def infer_review_source(review: AgentReview) -> str:
    token = slugify(getattr(review, "review_source", "unknown"))
    if token in {"subagent", "local", "service"}:
        return token
    profile = slugify(getattr(review, "provider_profile", ""))
    if "subagent" in profile:
        return "subagent"
    if any(marker in profile for marker in ("local", "repair", "root", "committee", "skill")):
        return "local"
    if profile:
        return "service"
    return "unknown"


def with_inferred_review_source(review: AgentReview) -> AgentReview:
    source = infer_review_source(review)
    if review.review_source == source:
        return review
    return review.model_copy(update={"review_source": source})


def summarize_review_sources(
    reviews: list[AgentReview],
    *,
    expected_subagent_reviews: int | None = None,
) -> dict[str, int | bool | None]:
    completed = [with_inferred_review_source(item) for item in reviews if item.status == "completed"]
    counts: dict[str, int] = {"subagent": 0, "local": 0, "service": 0, "unknown": 0}
    for review in completed:
        counts[review.review_source] = counts.get(review.review_source, 0) + 1
    missing = max((expected_subagent_reviews or 0) - counts["subagent"], 0) if expected_subagent_reviews is not None else 0
    return {
        "expected_subagent_reviews": expected_subagent_reviews,
        "completed_subagent_reviews": counts["subagent"],
        "completed_local_reviews": counts["local"],
        "completed_service_reviews": counts["service"],
        "completed_unknown_source_reviews": counts["unknown"],
        "missing_subagent_slots": missing,
        "full_subagent_committee": None if expected_subagent_reviews is None else missing == 0,
    }
