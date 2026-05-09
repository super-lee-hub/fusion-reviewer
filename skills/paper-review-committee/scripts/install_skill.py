#!/usr/bin/env python3
"""Install paper-review-committee skill to Codex or Claude Code skills directory."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

SKILL_SOURCE_DIR = Path(__file__).resolve().parents[1]  # skills/paper-review-committee/

FILES_TO_COPY = [
    "SKILL.md",
]

DIRS_TO_COPY = [
    "agents",
    "references",
    "scripts",
]

EXCLUDE_PATTERNS = [
    ".env",
    "*.pdf",
    "*.docx",
    "*.doc",
    "__pycache__",
    "*.pyc",
    ".pytest_cache",
    ".tmp",
    "review_outputs",
    "review_inputs",
    "tests",
    "data",
    "dist",
    "src",  # never vendor core library
]


def _should_exclude(name: str) -> bool:
    for pattern in EXCLUDE_PATTERNS:
        if pattern.startswith("*."):
            if name.endswith(pattern[1:]):
                return True
        elif name == pattern:
            return True
        elif pattern.endswith("/") and name == pattern.rstrip("/"):
            return True
    return False


def install_codex(target_dir: Path) -> Path:
    skill_dir = target_dir / "paper-review-committee"
    print(f"Installing to Codex: {skill_dir}")
    return _install(skill_dir)


def install_claude(scope: str, target_dir: Path | None = None) -> Path:
    if target_dir:
        skill_dir = target_dir
    elif scope == "personal":
        skill_dir = Path.home() / ".claude" / "skills" / "paper-review-committee"
    else:  # project
        skill_dir = Path.cwd() / ".claude" / "skills" / "paper-review-committee"
    print(f"Installing to Claude ({scope}): {skill_dir}")
    return _install(skill_dir)


def _install(skill_dir: Path) -> Path:
    if skill_dir.exists():
        shutil.rmtree(skill_dir)
    skill_dir.mkdir(parents=True, exist_ok=True)

    # Copy files
    for rel_name in FILES_TO_COPY:
        src = SKILL_SOURCE_DIR / rel_name
        if src.exists():
            shutil.copy2(src, skill_dir / rel_name)
            print(f"  {rel_name}")

    # Copy directories
    for dir_name in DIRS_TO_COPY:
        src_dir = SKILL_SOURCE_DIR / dir_name
        if not src_dir.exists():
            continue
        dest_dir = skill_dir / dir_name
        dest_dir.mkdir(parents=True, exist_ok=True)
        for item in src_dir.rglob("*"):
            if _should_exclude(item.name):
                continue
            if item.is_dir():
                continue
            rel_path = item.relative_to(src_dir)
            dest_path = dest_dir / rel_path
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest_path)
            print(f"  {dir_name}/{rel_path}")

    print(f"\nSkill installed to: {skill_dir}")
    return skill_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=["codex", "claude", "both"], default="both",
                        help="Installation target (default: both)")
    parser.add_argument("--scope", choices=["personal", "project"], default="personal",
                        help="Installation scope for Claude (default: personal)")
    parser.add_argument("--path", default=None,
                        help="Custom install path override")
    args = parser.parse_args(argv)

    if args.target in ("codex", "both"):
        codex_home = os.getenv("CODEX_HOME", str(Path.home() / ".codex"))
        target_dir = Path(args.path) if args.path else (Path(codex_home) / "skills")
        install_codex(target_dir)

    if args.target in ("claude", "both"):
        target_dir = Path(args.path) if args.path else None
        install_claude(scope=args.scope, target_dir=target_dir)

    print("\nNote: skill scripts require fusion_reviewer to be importable.")
    print("Run from the fusion-reviewer repo root: pip install -e .")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
