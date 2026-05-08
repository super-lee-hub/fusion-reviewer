from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from .config import ProviderProfile, load_provider_profiles
from .models import Concern, EditorReport


@dataclass
class ProviderOutput:
    payload: dict[str, Any]
    usage: dict[str, int]
    raw_text: str


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = (text or "").strip()
    if not stripped:
        raise ValueError("Provider returned empty text")
    fenced = re.search(r"```json\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1))
    direct = re.search(r"(\{.*\})", stripped, re.DOTALL)
    if direct:
        return json.loads(direct.group(1))
    return json.loads(stripped)


def _usage_payload(raw_usage: Any) -> dict[str, int]:
    if raw_usage is None:
        return {"requests": 1, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    input_tokens = int(getattr(raw_usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(raw_usage, "output_tokens", 0) or 0)
    total_tokens = int(getattr(raw_usage, "total_tokens", 0) or (input_tokens + output_tokens))
    return {
        "requests": 1,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


class BaseProvider:
    def __init__(self, name: str, profile: ProviderProfile):
        self.name = name
        self.profile = profile

    def health(self) -> dict[str, Any]:
        return {"profile": self.name, "ok": True, "message": "configured"}

    def generate(self, *, prompt: str) -> ProviderOutput:
        raise NotImplementedError

    def run_review(self, *, prompt: str, context: dict[str, Any]) -> ProviderOutput:
        raise NotImplementedError

    def run_editor(self, *, prompt: str, context: dict[str, Any]) -> ProviderOutput:
        raise NotImplementedError


class MockProvider(BaseProvider):
    _classify_overrides: dict[str, dict[str, Any]] = {}

    def _build_refs(self, evidence_refs: list[dict[str, Any]], count: int = 2) -> list[dict[str, Any]]:
        refs = evidence_refs[:count]
        if refs:
            return refs
        return [
            {
                "page": 1,
                "start_line": 1,
                "end_line": 2,
                "quote": "Mock evidence span generated for local testing.",
                "locator": "p.1 lines 1-2",
            }
        ]

    def run_review(self, *, prompt: str, context: dict[str, Any]) -> ProviderOutput:
        slot = context["slot"]
        evidence_refs = context.get("evidence_refs") or context.get("evidence_spans") or []
        summary = (
            f"{slot.title} found several revision-worthy issues using the shared evidence workspace. "
            f"This mock review is deterministic so the pipeline can run without external APIs."
        )
        strengths = [
            "The manuscript presents a coherent research narrative.",
            "The shared evidence view exposes enough text for grounded review.",
        ]
        weaknesses = [
            "Several claims would benefit from stronger supporting evidence.",
            "The current presentation leaves ambiguity around key contributions.",
        ]
        findings: list[dict[str, Any]] = []
        base_category = slot.category or "general"
        titles = [
            "Contribution framing needs clarification",
            "Core claims outpace supporting evidence",
        ]
        for idx, ref in enumerate(self._build_refs(evidence_refs, count=2), start=1):
            findings.append(
                {
                    "id": f"{slot.id}_finding_{idx}",
                    "issue_key": "contribution_framing_gap" if idx == 1 else "evidence_support_gap",
                    "title": titles[idx - 1],
                    "description": (
                        f"{slot.title} recommends revising the manuscript around "
                        f"{', '.join(slot.focus_areas[:2]) or 'core claims'}."
                    ),
                    "category": base_category,
                    "severity": "medium" if idx == 1 else "high",
                    "evidence_refs": [ref],
                    "needs_external_verification": False,
                    "recommendation": "Clarify the claim and align it more tightly with the cited evidence.",
                }
            )
        payload = {
            "summary": summary,
            "strengths": strengths,
            "weaknesses": weaknesses,
            "recommendation": "major_revision",
            "findings": findings,
        }
        return ProviderOutput(
            payload=payload,
            usage={"requests": 1, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            raw_text=json.dumps(payload),
        )

    def run_editor(self, *, prompt: str, context: dict[str, Any]) -> ProviderOutput:
        concerns: list[Concern] = context["concerns"]
        top_titles = [concern.title for concern in concerns[:3]]
        consensus = [concern.title for concern in concerns if concern.consensus_state == "consensus"][:5]
        disagreements = [concern.title for concern in concerns if concern.consensus_state != "consensus"][:5]
        payload = EditorReport(
            provider_profile=self.name,
            model=self.profile.model,
            decision="major_revision" if concerns else "minor_revision",
            consensus=consensus or ["Reviewers broadly agree the paper needs clearer support for its central claims."],
            disagreements=disagreements or ["Some issues were raised by only one reviewer and should be inspected manually."],
            priority_revisions=top_titles or ["Strengthen evidence-backed explanations for the main contribution."],
            decision_rationale="The manuscript shows promise, but the current draft has enough evidence-linked issues to require revision before acceptance.",
            markdown="",
        ).model_dump(mode="json")
        return ProviderOutput(
            payload=payload,
            usage={"requests": 1, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            raw_text=json.dumps(payload),
        )

    def generate(self, *, prompt: str) -> ProviderOutput:
        override_key = None
        if self._classify_overrides:
            for key in self._classify_overrides:
                if key in prompt:
                    override_key = key
                    break
        if override_key:
            payload = dict(self._classify_overrides[override_key])
            return ProviderOutput(
                payload=payload,
                usage={"requests": 1, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                raw_text=json.dumps(payload),
            )
        evidence_section = prompt.split("Indexed evidence:")[-1] if "Indexed evidence:" in prompt else prompt
        evidence_lower = evidence_section.lower()
        if "theoretical" in evidence_lower or "formal model" in evidence_lower or "proposition" in evidence_lower:
            coarse_family = "theoretical"
            labels = [
                {
                    "label": "formal_modeling",
                    "confidence": 0.85,
                    "primary": True,
                    "evidence_refs": [{"page": 1, "start_line": 1, "end_line": 2, "quote": "Mock theoretical evidence."}],
                }
            ]
        elif "empirical" in evidence_lower or "experiment" in evidence_lower or "randomized" in evidence_lower or "treatment" in evidence_lower:
            coarse_family = "empirical"
            labels = [
                {
                    "label": "experiment",
                    "confidence": 0.85,
                    "primary": True,
                    "evidence_refs": [{"page": 1, "start_line": 1, "end_line": 2, "quote": "Mock empirical evidence."}],
                }
            ]
        else:
            coarse_family = "mixed"
            labels = [
                {
                    "label": "conceptual_theory",
                    "confidence": 0.6,
                    "primary": True,
                    "evidence_refs": [{"page": 1, "start_line": 1, "end_line": 2, "quote": "Mock mixed evidence."}],
                }
            ]
        payload = {
            "coarse_family": coarse_family,
            "paradigm_labels": labels,
            "rationale": f"Mock classification: detected {coarse_family} paradigm from indexed evidence.",
        }
        return ProviderOutput(
            payload=payload,
            usage={"requests": 1, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            raw_text=json.dumps(payload),
        )


class OpenAICompatibleProvider(BaseProvider):
    def __init__(self, name: str, profile: ProviderProfile):
        super().__init__(name, profile)
        self.api_key = os.getenv(profile.api_key_env or "", "")
        self.client = None

    def _requires_base_url(self) -> bool:
        return self.name != "openai_default"

    def health(self) -> dict[str, Any]:
        missing_parts: list[str] = []
        if not self.api_key:
            missing_parts.append("api key")
        if self._requires_base_url() and not self.profile.base_url:
            missing_parts.append("base url")
        if missing_parts:
            return {"profile": self.name, "ok": False, "message": f"missing {' and '.join(missing_parts)}"}
        return {"profile": self.name, "ok": True, "message": "ready"}

    def _generate(self, prompt: str) -> ProviderOutput:
        if self._requires_base_url() and not self.profile.base_url:
            raise RuntimeError(f"Provider profile '{self.name}' requires a base_url")
        if not self.api_key:
            raise RuntimeError(f"Provider profile '{self.name}' requires an api key")
        if self.client is None:
            from openai import OpenAI

            self.client = OpenAI(api_key=self.api_key or None, base_url=self.profile.base_url or None)
        completion = self.client.chat.completions.create(
            model=self.profile.model,
            messages=[
                {"role": "system", "content": "Return valid JSON only. Be concise, critical, and evidence-grounded."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )
        text = completion.choices[0].message.content or ""
        return ProviderOutput(payload=_extract_json_object(text), usage=_usage_payload(completion.usage), raw_text=text)

    def generate(self, *, prompt: str) -> ProviderOutput:
        return self._generate(prompt)

    def run_review(self, *, prompt: str, context: dict[str, Any]) -> ProviderOutput:
        return self._generate(prompt)

    def run_editor(self, *, prompt: str, context: dict[str, Any]) -> ProviderOutput:
        return self._generate(prompt)


class AnthropicProvider(BaseProvider):
    def __init__(self, name: str, profile: ProviderProfile):
        super().__init__(name, profile)
        self.api_key = os.getenv(profile.api_key_env or "", "")
        self.client = None

    def health(self) -> dict[str, Any]:
        if not self.api_key:
            return {"profile": self.name, "ok": False, "message": "missing api key"}
        return {"profile": self.name, "ok": True, "message": "ready"}

    def _generate(self, prompt: str) -> ProviderOutput:
        if self.client is None:
            import anthropic

            self.client = anthropic.Anthropic(api_key=self.api_key)
        response = self.client.messages.create(
            model=self.profile.model,
            max_tokens=4000,
            temperature=0.1,
            system="Return valid JSON only. Be concise, critical, and evidence-grounded.",
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text if response.content else ""
        usage = {
            "requests": 1,
            "input_tokens": int(getattr(response.usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(response.usage, "output_tokens", 0) or 0),
            "total_tokens": int((getattr(response.usage, "input_tokens", 0) or 0) + (getattr(response.usage, "output_tokens", 0) or 0)),
        }
        return ProviderOutput(payload=_extract_json_object(text), usage=usage, raw_text=text)

    def generate(self, *, prompt: str) -> ProviderOutput:
        return self._generate(prompt)

    def run_review(self, *, prompt: str, context: dict[str, Any]) -> ProviderOutput:
        return self._generate(prompt)

    def run_editor(self, *, prompt: str, context: dict[str, Any]) -> ProviderOutput:
        return self._generate(prompt)


class GoogleProvider(BaseProvider):
    def __init__(self, name: str, profile: ProviderProfile):
        super().__init__(name, profile)
        self.api_key = os.getenv(profile.api_key_env or "", "")
        self._genai = None
        self.model = None

    def health(self) -> dict[str, Any]:
        if not self.api_key:
            return {"profile": self.name, "ok": False, "message": "missing api key"}
        return {"profile": self.name, "ok": True, "message": "ready"}

    def _generate(self, prompt: str) -> ProviderOutput:
        if self.model is None or self._genai is None:
            import google.generativeai as genai

            self._genai = genai
            self._genai.configure(api_key=self.api_key)
            self.model = self._genai.GenerativeModel(self.profile.model)
        response = self.model.generate_content(
            [
                "Return valid JSON only. Be concise, critical, and evidence-grounded.",
                prompt,
            ],
            generation_config=self._genai.GenerationConfig(temperature=0.1),
        )
        text = getattr(response, "text", "") or ""
        usage_meta = getattr(response, "usage_metadata", None)
        usage = {
            "requests": 1,
            "input_tokens": int(getattr(usage_meta, "prompt_token_count", 0) or 0),
            "output_tokens": int(getattr(usage_meta, "candidates_token_count", 0) or 0),
            "total_tokens": int(getattr(usage_meta, "total_token_count", 0) or 0),
        }
        return ProviderOutput(payload=_extract_json_object(text), usage=usage, raw_text=text)

    def generate(self, *, prompt: str) -> ProviderOutput:
        return self._generate(prompt)

    def run_review(self, *, prompt: str, context: dict[str, Any]) -> ProviderOutput:
        return self._generate(prompt)

    def run_editor(self, *, prompt: str, context: dict[str, Any]) -> ProviderOutput:
        return self._generate(prompt)


class ProviderRegistry:
    def __init__(self) -> None:
        self.profiles = load_provider_profiles()

    def names(self) -> list[str]:
        return sorted(self.profiles.keys())

    def get_profile(self, name: str) -> ProviderProfile:
        if name not in self.profiles:
            raise KeyError(f"Unknown provider profile: {name}")
        return self.profiles[name]

    def build(self, name: str, model_override: str | None = None) -> BaseProvider:
        profile = self.get_profile(name).model_copy(deep=True)
        if model_override:
            profile.model = model_override
        if profile.type == "openai_compatible":
            return OpenAICompatibleProvider(name, profile)
        vendor = (profile.vendor or "").lower()
        if vendor == "anthropic":
            return AnthropicProvider(name, profile)
        if vendor == "google":
            return GoogleProvider(name, profile)
        return MockProvider(name, profile)

    def health_report(self) -> list[dict[str, Any]]:
        return [self.build(name).health() for name in self.names()]
