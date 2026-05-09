"""Schema validation and version compatibility — no LLM/API calls."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "skills" / "paper-review-committee" / "references" / "schemas"

CURRENT_VERSION = "1.0.0"
SUPPORTED_LEGACY_VERSIONS: set[str] = set()  # add legacy versions here as needed


class SchemaValidationError(Exception):
    """Raised when schema validation fails."""


class UnsupportedVersionError(SchemaValidationError):
    """Raised when an artifact version is newer than supported."""


def _load_schema(name: str) -> dict[str, Any]:
    path = SCHEMA_DIR / name
    if not path.exists():
        raise SchemaValidationError(f"Schema file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _check_version(artifact_version: str | None) -> None:
    if artifact_version is None:
        return  # no version = pre-versioning artifact, treat as legacy
    if artifact_version == CURRENT_VERSION:
        return
    if artifact_version in SUPPORTED_LEGACY_VERSIONS:
        return
    # Simple semver comparison: major version change = unsupported
    try:
        art_major = int(artifact_version.split(".")[0])
        cur_major = int(CURRENT_VERSION.split(".")[0])
        if art_major > cur_major:
            raise UnsupportedVersionError(
                f"Artifact version {artifact_version} is newer than supported version {CURRENT_VERSION}"
            )
    except (ValueError, IndexError):
        pass
    # Older major version: supported as legacy with migration
    SUPPORTED_LEGACY_VERSIONS.add(artifact_version)


def validate_against_schema(data: dict[str, Any], schema_name: str) -> dict[str, Any]:
    """Validate data against a named JSON schema.

    Returns diagnostics dict with ``valid``, ``errors``, and ``warnings``.
    Currently performs structural checks (required fields, types).
    Full JSON Schema validation can be added with jsonschema library.
    """
    schema = _load_schema(schema_name)
    diagnostics: dict[str, Any] = {"valid": True, "errors": [], "warnings": []}

    # Check required fields
    required = schema.get("required", [])
    for field in required:
        if field not in data:
            diagnostics["valid"] = False
            diagnostics["errors"].append(f"Missing required field: {field}")

    # Check version compatibility
    version = data.get("schema_version") or data.get("artifact_contract_version")
    try:
        _check_version(version)
    except UnsupportedVersionError as e:
        diagnostics["valid"] = False
        diagnostics["errors"].append(str(e))
        return diagnostics

    # Check types for properties
    properties = schema.get("properties", {})
    for prop_name, prop_schema in properties.items():
        if prop_name not in data:
            continue
        expected_type = prop_schema.get("type")
        if expected_type and not _check_type(data[prop_name], expected_type):
            diagnostics["warnings"].append(
                f"Type mismatch for '{prop_name}': expected {expected_type}"
            )

    return diagnostics


def _check_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True  # unknown type, skip check


def validate_reviewer_output(data: dict[str, Any]) -> dict[str, Any]:
    return validate_against_schema(data, "reviewer.schema.json")


def validate_editor_output(data: dict[str, Any]) -> dict[str, Any]:
    return validate_against_schema(data, "editor.schema.json")
