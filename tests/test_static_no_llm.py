"""Static analysis: forbid LLM/API imports in core/scripts."""

import ast
import sys
from pathlib import Path

FORBIDDEN_IMPORTS = {
    "openai",
    "anthropic",
    "google.generativeai",
    "google.generativeai.types",
    "fusion_reviewer.providers",
}

SRC_DIR = Path(__file__).resolve().parents[1] / "src" / "fusion_reviewer"


def _find_python_files(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*.py")
        if "__pycache__" not in str(p) and p.name != "__init__.py"
    )


def _check_file(path: Path) -> list[str]:
    violations: list[str] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return [f"{path}: syntax error"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if any(alias.name == forbidden or alias.name.startswith(forbidden + ".") for forbidden in FORBIDDEN_IMPORTS):
                    violations.append(f"{path}: imports {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                for forbidden in FORBIDDEN_IMPORTS:
                    if node.module == forbidden or node.module.startswith(forbidden + "."):
                        violations.append(f"{path}: imports from {node.module}")
    return violations


def test_no_llm_imports_in_core():
    violations: list[str] = []
    for py_file in _find_python_files(SRC_DIR):
        violations.extend(_check_file(py_file))

    if violations:
        msg = "Forbidden LLM/API imports found:\n" + "\n".join(violations)
        raise AssertionError(msg)


def test_no_provider_imports_in_core():
    """Providers module is deleted — nothing should import from it."""
    for py_file in _find_python_files(SRC_DIR):
        content = py_file.read_text(encoding="utf-8")
        if "from .providers import" in content or "from fusion_reviewer.providers import" in content:
            raise AssertionError(f"{py_file}: imports from deleted providers module")
