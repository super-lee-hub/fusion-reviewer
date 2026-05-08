from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest


def _load_finalize_run_script():
    script_path = Path(__file__).resolve().parents[1] / "codex-skill" / "scripts" / "finalize_run.py"
    module_name = f"finalize_run_script_{uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_finalize_run_script_defaults_to_reviews_rebuild_mode(tmp_path, monkeypatch):
    module = _load_finalize_run_script()
    captured: dict[str, object] = {}

    def fake_rebuild(run_dir, *, title=None, source_name=None, reviews_dir=None, editor_file=None, force_docx_evidence=False):
        captured.update(
            {
                "run_dir": run_dir,
                "title": title,
                "source_name": source_name,
                "reviews_dir": reviews_dir,
                "editor_file": editor_file,
                "force_docx_evidence": force_docx_evidence,
            }
        )
        return {"ok": True, "mode": "rebuild"}

    monkeypatch.setattr(module, "rebuild_codex_run_from_reviews", fake_rebuild)
    monkeypatch.setattr(module, "finalize_codex_run", lambda *args, **kwargs: pytest.fail("legacy finalize should not run"))

    run_dir = tmp_path / "prepared run"
    exit_code = module.main(["--run-dir", str(run_dir), "--force-docx-evidence"])

    assert exit_code == 0
    assert captured["run_dir"] == str(run_dir)
    assert captured["reviews_dir"] is None
    assert captured["title"] is None
    assert captured["source_name"] is None
    assert captured["editor_file"] is None
    assert captured["force_docx_evidence"] is True


def test_finalize_run_script_uses_legacy_mode_when_reviews_file_is_provided(tmp_path, monkeypatch):
    module = _load_finalize_run_script()
    reviews_file = tmp_path / "reviews.json"
    editor_file = tmp_path / "editor.json"
    reviews_file.write_text(json.dumps([{"agent_id": "a"}], ensure_ascii=False), encoding="utf-8")
    editor_file.write_text(json.dumps({"decision": "major_revision"}, ensure_ascii=False), encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_finalize(run_dir, *, title, source_name, reviews, editor):
        captured.update(
            {
                "run_dir": run_dir,
                "title": title,
                "source_name": source_name,
                "reviews": reviews,
                "editor": editor,
            }
        )
        return {"ok": True, "mode": "legacy"}

    monkeypatch.setattr(module, "finalize_codex_run", fake_finalize)
    monkeypatch.setattr(
        module,
        "rebuild_codex_run_from_reviews",
        lambda *args, **kwargs: pytest.fail("rebuild mode should not run when reviews-file is provided"),
    )

    run_dir = tmp_path / "prepared run"
    exit_code = module.main(
        [
            "--run-dir",
            str(run_dir),
            "--title",
            "测试论文",
            "--source-name",
            "paper.docx",
            "--reviews-file",
            str(reviews_file),
            "--editor-file",
            str(editor_file),
        ]
    )

    assert exit_code == 0
    assert captured["run_dir"] == str(run_dir)
    assert captured["title"] == "测试论文"
    assert captured["source_name"] == "paper.docx"
    assert captured["reviews"] == [{"agent_id": "a"}]
    assert captured["editor"] == {"decision": "major_revision"}


def test_cli_finalize_from_payloads_invokes_formal_payload_entrypoint(monkeypatch):
    from fusion_reviewer.cli import cli
    from click.testing import CliRunner

    runner = CliRunner()
    captured: dict[str, object] = {}

    def fake_payload_finalize(run_dir, *, reviews_file, editor_file, title=None, source_name=None):
        captured.update(
            {
                "run_dir": run_dir,
                "reviews_file": reviews_file,
                "editor_file": editor_file,
                "title": title,
                "source_name": source_name,
            }
        )
        return {"ok": True}

    monkeypatch.setattr("fusion_reviewer.cli.finalize_codex_run_from_payload_files", fake_payload_finalize)

    with runner.isolated_filesystem():
        Path("run_dir").mkdir()
        Path("tmp_reviews.json").write_text("[]", encoding="utf-8")
        Path("tmp_editor.json").write_text("{}", encoding="utf-8")
        result = runner.invoke(
            cli,
            [
                "codex",
                "finalize-from-payloads",
                "--run-dir",
                "run_dir",
                "--reviews-file",
                "tmp_reviews.json",
                "--editor-file",
                "tmp_editor.json",
            ],
        )

    assert result.exit_code == 0
    assert Path(captured["run_dir"]).name == "run_dir"
    assert Path(captured["reviews_file"]).name == "tmp_reviews.json"
    assert Path(captured["editor_file"]).name == "tmp_editor.json"


def test_cli_finalize_from_payloads_can_write_json_to_output_file(monkeypatch):
    from fusion_reviewer.cli import cli
    from click.testing import CliRunner

    runner = CliRunner()

    monkeypatch.setattr(
        "fusion_reviewer.cli.finalize_codex_run_from_payload_files",
        lambda *args, **kwargs: {"decision": "major_revision", "title": "测试论文"},
    )

    with runner.isolated_filesystem():
        Path("run_dir").mkdir()
        Path("tmp_reviews.json").write_text("[]", encoding="utf-8")
        Path("tmp_editor.json").write_text("{}", encoding="utf-8")
        result = runner.invoke(
            cli,
            [
                "codex",
                "finalize-from-payloads",
                "--run-dir",
                "run_dir",
                "--reviews-file",
                "tmp_reviews.json",
                "--editor-file",
                "tmp_editor.json",
                "--output-file",
                "summary.json",
            ],
        )
        written = Path("summary.json").read_text(encoding="utf-8")

    assert result.exit_code == 0
    assert "Wrote JSON output to summary.json" in result.output
    assert '"decision": "major_revision"' in written
