from __future__ import annotations

import json
from pathlib import Path

import click
import uvicorn

from .codex_repair import (
    finalize_codex_run_from_payload_files,
    rebuild_codex_run_from_reviews,
    repair_codex_run,
)
from .config import get_settings
from .providers import ProviderRegistry
from .service import JobService
from .web import create_app


def _echo_json(payload: object, *, output_file: Path | None = None) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if output_file is not None:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(rendered, encoding="utf-8")
        click.echo(f"Wrote JSON output to {output_file}")
        return
    click.echo(rendered)


def _resolve_paper_path(paper_path: Path | None, pdf_path: Path | None) -> Path:
    resolved = paper_path or pdf_path
    if resolved is None:
        raise click.ClickException("Provide --paper <path> (or legacy --pdf <path>).")
    return resolved


@click.group()
def cli() -> None:
    """fusion-review command line interface."""


@cli.command()
@click.option("--paper", "paper_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None)
@click.option("--pdf", "pdf_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None, help="Legacy alias for --paper")
@click.option("--title", default=None)
@click.option("--wait-seconds", type=int, default=None)
@click.option("--provider-profile", default=None, help="Override all slot profiles with one provider profile.")
@click.option("--mode", type=click.Choice(["backend", "codex"]), default="backend", show_default=True)
@click.option("--journal-text", default=None, help="Optional journal requirements pasted as text.")
@click.option(
    "--journal-file",
    "journal_file_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Optional file containing journal requirements.",
)
@click.option("--revision-text", default=None, help="Revision notes / response letter text.")
@click.option(
    "--revision-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Revision notes / response letter file.",
)
def submit(
    paper_path: Path | None,
    pdf_path: Path | None,
    title: str | None,
    wait_seconds: int | None,
    provider_profile: str | None,
    mode: str,
    journal_text: str | None,
    journal_file_path: Path | None,
    revision_text: str | None,
    revision_file: Path | None,
) -> None:
    service = JobService()
    settings = get_settings()
    paper = _resolve_paper_path(paper_path, pdf_path)
    job = service.submit_file(
        paper_path=paper,
        title=title,
        provider_override=provider_profile,
        mode=mode,
        journal_text=journal_text,
        journal_file_path=journal_file_path,
        revision_text=revision_text,
        revision_file_path=revision_file,
    )
    timeout = settings.default_wait_seconds if wait_seconds is None else wait_seconds
    if mode == "backend" and timeout > 0:
        job = service.wait(str(job.id), timeout) or job
    _echo_json(job.model_dump(mode="json"))


@cli.command()
@click.option("--job-id", required=True)
def status(job_id: str) -> None:
    service = JobService()
    job = service.get_status(job_id)
    if job is None:
        raise click.ClickException(f"Job not found: {job_id}")
    _echo_json(job.model_dump(mode="json"))


@cli.command()
@click.option("--job-id", required=True)
def result(job_id: str) -> None:
    service = JobService()
    _echo_json(service.result(job_id))


@cli.command()
@click.option("--host", default=None)
@click.option("--port", type=int, default=None)
def serve(host: str | None, port: int | None) -> None:
    settings = get_settings()
    uvicorn.run(create_app(), host=host or settings.web_host, port=port or settings.web_port)


@cli.group("providers")
def providers_group() -> None:
    """Provider utilities."""


@providers_group.command("test")
def providers_test() -> None:
    registry = ProviderRegistry()
    _echo_json({"profiles": registry.health_report()})


@cli.group("codex")
def codex_group() -> None:
    """Codex skill / run maintenance utilities."""


@codex_group.command("repair-run")
@click.option("--run-dir", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path), help="需要修复的 Codex run 目录")
@click.option("--title", default=None, help="可选：覆盖自动识别的论文标题")
@click.option("--source-name", default=None, help="可选：覆盖自动识别的原始文件名")
@click.option("--force-docx-evidence", is_flag=True, help="即使质量检查未触发，也强制把 DOCX 原生文本重写为权威 evidence")
@click.option("--output-file", default=None, type=click.Path(dir_okay=False, path_type=Path), help="可选：把 JSON 结果写到 UTF-8 文件，避免控制台编码问题")
def codex_repair_run(
    run_dir: Path,
    title: str | None,
    source_name: str | None,
    force_docx_evidence: bool,
    output_file: Path | None,
) -> None:
    payload = repair_codex_run(
        run_dir,
        title=title,
        source_name=source_name,
        force_docx_evidence=force_docx_evidence,
    )
    _echo_json(payload, output_file=output_file)


@codex_group.command("finalize-from-reviews")
@click.option("--run-dir", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path), help="Codex run 目录")
@click.option(
    "--reviews-dir",
    default=None,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="可选：显式指定 reviewer JSON 所在目录，默认使用 <run-dir>/reviews",
)
@click.option(
    "--editor-file",
    default=None,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="可选：显式提供 editor JSON；不传时会优先复用 editor_input.json，否则根据 reviewer 自动生成",
)
@click.option("--title", default=None, help="可选：覆盖自动识别的论文标题")
@click.option("--source-name", default=None, help="可选：覆盖自动识别的原始文件名")
@click.option("--force-docx-evidence", is_flag=True, help="即使质量检查未触发，也强制把 DOCX 原生文本重写为权威 evidence")
@click.option("--provider-profile", default=None, help="可选：收口阶段需要调用模型时使用的 provider profile；返修回应审稿需要它")
@click.option("--output-file", default=None, type=click.Path(dir_okay=False, path_type=Path), help="可选：把 JSON 结果写到 UTF-8 文件，避免控制台编码问题")
def codex_finalize_from_reviews(
    run_dir: Path,
    reviews_dir: Path | None,
    editor_file: Path | None,
    title: str | None,
    source_name: str | None,
    force_docx_evidence: bool,
    provider_profile: str | None,
    output_file: Path | None,
) -> None:
    kwargs = {
        "title": title,
        "source_name": source_name,
        "reviews_dir": reviews_dir,
        "editor_file": editor_file,
        "force_docx_evidence": force_docx_evidence,
    }
    if provider_profile:
        kwargs["provider_profile"] = provider_profile
    payload = rebuild_codex_run_from_reviews(run_dir, **kwargs)
    _echo_json(payload, output_file=output_file)


@codex_group.command("finalize-from-payloads")
@click.option("--run-dir", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path), help="Codex run 目录")
@click.option(
    "--reviews-file",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="包含 reviewer JSON 列表的 UTF-8 文件",
)
@click.option(
    "--editor-file",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="包含 editor JSON 的 UTF-8 文件",
)
@click.option("--title", default=None, help="可选：覆盖自动识别的论文标题")
@click.option("--source-name", default=None, help="可选：覆盖自动识别的原始文件名")
@click.option("--provider-profile", default=None, help="可选：收口阶段需要调用模型时使用的 provider profile；返修回应审稿需要它")
@click.option("--output-file", default=None, type=click.Path(dir_okay=False, path_type=Path), help="可选：把 JSON 结果写到 UTF-8 文件，避免控制台编码问题")
def codex_finalize_from_payloads(
    run_dir: Path,
    reviews_file: Path,
    editor_file: Path,
    title: str | None,
    source_name: str | None,
    provider_profile: str | None,
    output_file: Path | None,
) -> None:
    kwargs = {
        "reviews_file": reviews_file,
        "editor_file": editor_file,
        "title": title,
        "source_name": source_name,
    }
    if provider_profile:
        kwargs["provider_profile"] = provider_profile
    payload = finalize_codex_run_from_payload_files(run_dir, **kwargs)
    _echo_json(payload, output_file=output_file)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
