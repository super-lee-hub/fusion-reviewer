#!/usr/bin/env python3
"""为 Codex 委员会审稿准备共享证据包。"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path


def _iter_repo_candidates() -> list[Path]:
    candidates: list[Path] = []
    env_root = os.getenv("FUSION_REVIEWER_REPO_ROOT")
    if env_root:
        candidates.append(Path(env_root).expanduser())
    deepreview_root = os.getenv("DEEPREVIEW_ROOT")
    if deepreview_root:
        candidates.append(Path(deepreview_root).expanduser())

    script_path = Path(__file__).resolve()
    search_roots = [Path.cwd(), *Path.cwd().parents, script_path.parent, *script_path.parents]
    for root in search_roots:
        candidates.append(root)
        candidates.append(root / "fusion-reviewer")

    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _module_importable(name: str) -> bool:
    try:
        importlib.import_module(name)
    except Exception:
        return False
    return True


def _ensure_import_paths() -> None:
    if _module_importable("fusion_reviewer") and _module_importable("deepreview"):
        return

    for candidate in _iter_repo_candidates():
        src_dir = candidate / "src"
        package_dir = src_dir / "fusion_reviewer"
        if package_dir.exists():
            sys.path.insert(0, str(src_dir))
        for deepreview_dir in (
            candidate,
            candidate / "DeepReviewer-v2",
            candidate.parent / "DeepReviewer-v2",
        ):
            if (deepreview_dir / "deepreview").exists():
                sys.path.insert(0, str(deepreview_dir))
                break


_ensure_import_paths()

from fusion_reviewer.codex_runtime import prepare_codex_run  # noqa: E402


def _print_environment_summary(manifest: dict[str, object]) -> None:
    env_status = manifest.get("environment_status") or {}
    if not isinstance(env_status, dict):
        return
    print("环境自检：", file=sys.stderr)
    print(f"- fusion_reviewer 可导入：{env_status.get('fusion_reviewer_importable')}", file=sys.stderr)
    print(f"- deepreview 可导入：{env_status.get('deepreview_importable')}", file=sys.stderr)
    print(f"- MinerU token 已配置：{env_status.get('mineru_token_present')}", file=sys.stderr)
    print(f"- LibreOffice 可用：{env_status.get('libreoffice_available')}", file=sys.stderr)
    if env_status.get("libreoffice_path"):
        print(f"- LibreOffice 路径：{env_status.get('libreoffice_path')}", file=sys.stderr)
    finalize_command = str(manifest.get("recommended_finalize_command") or "").strip()
    fallback_command = str(manifest.get("recommended_finalize_fallback_command") or "").strip()
    if finalize_command:
        print("- reviewer 写完后，请直接用正式收口命令：", file=sys.stderr)
        print(f"  {finalize_command}", file=sys.stderr)
    if fallback_command:
        print("- 如果 CLI 不可用，可退回脚本入口：", file=sys.stderr)
        print(f"  {fallback_command}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper", required=True, help="论文文件路径，支持 PDF / DOCX / DOC")
    parser.add_argument(
        "--output-root",
        default=None,
        help="可选，输出根目录；不填时默认使用 fusion-reviewer 配置里的 DATA_DIR",
    )
    parser.add_argument("--run-id", default=None, help="可选，固定本次 run id")
    parser.add_argument("--journal-text", default=None, help="可选，直接传入期刊风格或审稿要求文本")
    parser.add_argument("--journal-file", default=None, help="可选，期刊要求文件路径，建议使用 txt / md / docx")
    parser.add_argument("--revision-text", default=None, help="可选，返修说明 / 作者答复文本")
    parser.add_argument("--revision-file", default=None, help="可选，返修说明 / 作者答复文件路径，支持 txt / md / docx")
    parser.add_argument("--previous-review-dir", default=None, help="可选，上一轮审稿产物目录；用于返修回应审稿")
    parser.add_argument("--previous-review-file", default=None, help="可选，上一轮审稿意见文件；优先级高于 --previous-review-dir")
    args = parser.parse_args(argv)

    try:
        manifest = prepare_codex_run(
            args.paper,
            output_root=args.output_root,
            run_id=args.run_id,
            journal_text=args.journal_text,
            journal_file_path=args.journal_file,
            revision_text=args.revision_text,
            revision_file=args.revision_file,
            previous_review_dir=args.previous_review_dir,
            previous_review_file=args.previous_review_file,
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    _print_environment_summary(manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
