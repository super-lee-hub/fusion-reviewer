#!/usr/bin/env python3
"""Install this skill into the current user's Codex skills directory."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


SKILL_NAME = "paper-review-committee"


def _default_codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()


def _default_dest() -> Path:
    return _default_codex_home() / "skills" / SKILL_NAME


def _safe_remove(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def _copy_tree(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    _safe_remove(destination)
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".DS_Store"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=None, help="Skill source directory. Defaults to the codex-skill root.")
    parser.add_argument(
        "--dest",
        default=str(_default_dest()),
        help="Destination skill directory. Defaults to $CODEX_HOME/skills/paper-review-committee or ~/.codex/skills/paper-review-committee.",
    )
    args = parser.parse_args(argv)

    script_path = Path(__file__).resolve()
    default_source = script_path.parent.parent
    source = Path(args.source).resolve() if args.source else default_source
    destination = Path(args.dest).resolve()

    if not (source / "SKILL.md").exists():
        print(f"error: SKILL.md not found under {source}", file=sys.stderr)
        return 1

    skills_root = (_default_codex_home() / "skills").resolve()
    if destination.name != SKILL_NAME or not destination.is_relative_to(skills_root):
        print(
            f"error: refusing to install outside {skills_root} or under a different skill name: {destination}",
            file=sys.stderr,
        )
        return 1

    _copy_tree(source, destination)
    print(f"installed skill to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
