#!/usr/bin/env python3
"""把 reviewer 和 editor 的 JSON 收口成稳定的 Codex 审稿产物。

默认优先走正式的 reviews/ 收口路径，不需要再手写临时脚本拼 reviewer 结果。
"""

from __future__ import annotations

import argparse
import inspect
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

from fusion_reviewer.codex_repair import rebuild_codex_run_from_reviews  # noqa: E402
from fusion_reviewer.codex_runtime import finalize_codex_run  # noqa: E402


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _call_with_optional_provider(func, *args, provider_profile: str | None = None, **kwargs):
    if provider_profile and "provider_profile" in inspect.signature(func).parameters:
        kwargs["provider_profile"] = provider_profile
    return func(*args, **kwargs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, help="prepare_paper.py 生成的 run 目录")
    parser.add_argument("--title", default=None, help="可选：论文标题；不传时自动从 run 目录推断")
    parser.add_argument("--source-name", default=None, help="可选：原始文件名；不传时自动从 run 目录推断")
    parser.add_argument("--reviews-file", default=None, help="包含 reviewer JSON 列表的文件")
    parser.add_argument(
        "--reviews-dir",
        default=None,
        help="可选：直接从 reviews/ 目录读取 reviewer JSON；不传时默认使用 <run-dir>/reviews",
    )
    parser.add_argument(
        "--editor-file",
        default=None,
        help="可选：包含 editor / meta-review JSON 的文件；走 reviews/ 收口时可省略",
    )
    parser.add_argument(
        "--force-docx-evidence",
        action="store_true",
        help="当使用 --reviews-dir 重建产物时，即使质量检查未触发，也强制把 DOCX 原生文本重写为权威 evidence",
    )
    parser.add_argument(
        "--provider-profile",
        default=None,
        help="可选：收口阶段需要调用模型时使用的 provider profile；返修回应审稿需要它",
    )
    args = parser.parse_args(argv)

    try:
        if args.reviews_file:
            if args.reviews_dir:
                parser.error("不能同时传 --reviews-file 和 --reviews-dir；请二选一。")
            if not args.editor_file:
                parser.error("legacy 模式需要同时传入 --reviews-file 和 --editor-file。")
            if not args.title or not args.source_name:
                parser.error("legacy 模式需要同时传入 --title 和 --source-name；或者改用默认的 reviews/ 收口模式。")
            payload = _call_with_optional_provider(
                finalize_codex_run,
                args.run_dir,
                title=args.title,
                source_name=args.source_name,
                reviews=_load_json(Path(args.reviews_file)),
                editor=_load_json(Path(args.editor_file)),
                provider_profile=args.provider_profile,
            )
        else:
            payload = _call_with_optional_provider(
                rebuild_codex_run_from_reviews,
                args.run_dir,
                title=args.title,
                source_name=args.source_name,
                reviews_dir=args.reviews_dir,
                editor_file=args.editor_file,
                force_docx_evidence=args.force_docx_evidence,
                provider_profile=args.provider_profile,
            )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
