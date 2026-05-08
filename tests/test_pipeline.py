from pathlib import Path

from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from fusion_reviewer import config as fusion_config
from fusion_reviewer.codex_runtime import prepare_codex_run
from fusion_reviewer.service import JobService
from fusion_reviewer.web import create_app


def _clear_config_caches() -> None:
    fusion_config.get_settings.cache_clear()
    fusion_config.load_provider_profiles.cache_clear()
    fusion_config.load_review_plan.cache_clear()


def _write_sample_pdf(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=letter)
    y = 750
    for line in [
        "Mock paper title",
        "This paper proposes a simple method.",
        "The evidence for the strongest claim is limited.",
        "More experiments and clearer framing are needed.",
    ]:
        c.drawString(72, y, line)
        y -= 20
    c.save()


def test_web_health_endpoint():
    client = TestClient(create_app())
    response = client.get("/providers/health")
    assert response.status_code == 200
    assert "profiles" in response.json()


def test_mock_pipeline_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    _clear_config_caches()
    pdf_path = tmp_path / "paper.pdf"
    _write_sample_pdf(pdf_path)

    service = JobService()
    job = service.submit_file(
        paper_path=pdf_path,
        journal_text="目标期刊偏好理论与方法并重，要求明确说明创新点与适配性。",
    )
    final_state = service.wait(str(job.id), 60)

    assert final_state is not None
    assert final_state.status == "completed"
    assert final_state.journal_context_present is True
    artifacts = service.artifacts(str(job.id))
    assert "final_report.pdf" in artifacts["artifacts"]
    assert "final_report.md" in artifacts["artifacts"]
    assert "meta_review.md" in artifacts["artifacts"]
    assert "evidence/normalized.md" in artifacts["artifacts"]
    assert "evidence/page_index.json" in artifacts["artifacts"]
    assert "evidence/journal_requirements.md" in artifacts["artifacts"]

    latest_dir = Path(final_state.metadata["latest_results_dir"])
    paper_dir = Path(final_state.metadata["paper_results_dir"])
    assert latest_dir.exists()
    assert paper_dir.exists()
    assert (latest_dir / "01-审稿总报告.md").exists()
    assert (latest_dir / "03-元审稿.md").exists()
    assert (latest_dir / "04-问题汇总.csv").exists()
    assert (latest_dir / "10-Reviewer逐份意见" / "11-委员会审稿-A.md").exists()

    result = service.result(str(job.id))
    assert result["job"]["decision"] == "major_revision"
    assert result["job"]["journal_context_present"] is True
    assert result["final_summary"]["journal_context_present"] is True
    assert "mineru_attempted" in result["final_summary"]
    assert len(result["concerns"]) >= 1

    _clear_config_caches()


def test_codex_mode_creates_prepared_run(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    _clear_config_caches()
    pdf_path = tmp_path / "paper.pdf"
    _write_sample_pdf(pdf_path)

    service = JobService()
    job = service.submit_file(paper_path=pdf_path, mode="codex")
    state = service.get_status(str(job.id))

    assert state is not None
    assert state.mode == "codex"
    assert state.status == "queued"
    artifacts = service.artifacts(str(job.id))
    assert "job.json" in artifacts["artifacts"]
    assert any(name.startswith("source_input.") for name in artifacts["artifacts"])

    _clear_config_caches()


def test_prepare_codex_run_uses_configured_data_dir_by_default(tmp_path, monkeypatch):
    data_dir = tmp_path / "统一输出"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    _clear_config_caches()
    pdf_path = tmp_path / "paper.pdf"
    _write_sample_pdf(pdf_path)

    manifest = prepare_codex_run(
        pdf_path,
        journal_text="目标期刊更强调理论贡献、方法透明和写作规范。",
    )

    run_dir = Path(str(manifest["run_dir"]))
    assert run_dir.parent == data_dir
    assert manifest["output_root"] == str(data_dir)
    assert manifest["journal_context_present"] is True
    assert (run_dir / "evidence" / "journal_requirements.md").exists()
    assert "fusion-review codex finalize-from-reviews" in manifest["recommended_finalize_command"]
    assert "python .\\codex-skill\\scripts\\finalize_run.py" in manifest["recommended_finalize_fallback_command"]
    prepare_note = (run_dir / "00-当前仅完成预处理.txt").read_text(encoding="utf-8")
    assert "不要手写 tmp_finalize_*.py" in prepare_note
    assert str(run_dir) in prepare_note
    diagnostics = (run_dir / "evidence" / "diagnostics.json").read_text(encoding="utf-8")
    assert "journal_context_present" in diagnostics

    _clear_config_caches()
