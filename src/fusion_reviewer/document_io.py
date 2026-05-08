from __future__ import annotations

import hashlib
import json
import locale
import os
import shutil
import subprocess
from dataclasses import asdict
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from xml.etree import ElementTree as ET

try:  # pragma: no cover - optional dependency
    import fitz  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    fitz = None  # type: ignore

try:  # pragma: no cover - optional dependency
    from pypdf import PdfReader  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    PdfReader = None  # type: ignore

DocumentKind = Literal["pdf", "docx", "doc"]
LayoutFidelity = Literal["full", "degraded", "text_only"]


@dataclass(slots=True)
class ArtifactPaths:
    root: Path
    cache_dir: Path
    source_copy_path: Path
    normalized_source_path: Path
    markdown_path: Path
    plain_text_path: Path
    page_index_path: Path
    structured_json_path: Path
    diagnostics_path: Path
    manifest_path: Path
    snapshots_dir: Path

    def as_dict(self) -> dict[str, str]:
        return {key: str(value) for key, value in asdict(self).items()}


@dataclass(slots=True)
class PageRecord:
    page_number: int
    text: str
    blocks: list[dict[str, Any]] = field(default_factory=list)
    images: int = 0
    used_ocr: bool = False
    scanned_candidate: bool = False
    low_quality: bool = False


@dataclass(slots=True)
class NormalizedDocument:
    source_path: Path
    document_kind: DocumentKind
    cache_key: str
    cache_hit: bool
    layout_fidelity: LayoutFidelity
    extractor_used: str
    conversion_used: str | None
    warning: str | None
    artifacts: ArtifactPaths
    normalized_source_path: Path
    markdown: str
    plain_text: str
    page_index: dict[int, list[str]]
    structured_pages: list[dict[str, Any]]
    diagnostics: dict[str, Any]
    snapshot_paths: list[Path]
    content_list: list[dict[str, Any]] | None = None


def detect_document_kind(path: Path) -> DocumentKind:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".docx":
        return "docx"
    if suffix == ".doc":
        return "doc"
    raise ValueError(f"Unsupported document type: {path.suffix}")


def compute_cache_key(path: Path, *, salt: str = "") -> str:
    stat = path.stat()
    payload = f"{path.resolve()}::{stat.st_size}::{stat.st_mtime_ns}::{salt}".encode("utf-8")
    return hashlib.md5(payload).hexdigest()


def build_artifact_paths(output_root: Path, source_path: Path, cache_key: str) -> ArtifactPaths:
    cache_dir = output_root / source_path.stem / cache_key
    snapshots_dir = cache_dir / "snapshots"
    return ArtifactPaths(
        root=output_root,
        cache_dir=cache_dir,
        source_copy_path=cache_dir / f"source{source_path.suffix.lower()}",
        normalized_source_path=cache_dir / "normalized.pdf",
        markdown_path=cache_dir / "normalized.md",
        plain_text_path=cache_dir / "plain_text.txt",
        page_index_path=cache_dir / "page_index.json",
        structured_json_path=cache_dir / "structured.json",
        diagnostics_path=cache_dir / "diagnostics.json",
        manifest_path=cache_dir / "manifest.json",
        snapshots_dir=snapshots_dir,
    )


def ensure_output_root(output_root: Path) -> None:
    output_root.mkdir(parents=True, exist_ok=True)


def is_cache_fresh(source_path: Path, artifacts: ArtifactPaths) -> bool:
    required = [
        artifacts.source_copy_path,
        artifacts.normalized_source_path,
        artifacts.markdown_path,
        artifacts.plain_text_path,
        artifacts.page_index_path,
        artifacts.structured_json_path,
        artifacts.diagnostics_path,
        artifacts.manifest_path,
    ]
    if not all(path.exists() for path in required):
        return False
    try:
        manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))
        stat = source_path.stat()
        return (
            manifest.get("source_size") == stat.st_size
            and manifest.get("source_mtime_ns") == stat.st_mtime_ns
            and manifest.get("source_suffix") == source_path.suffix.lower()
        )
    except Exception:
        return False


def load_cached_document(
    source_path: Path,
    document_kind: DocumentKind,
    cache_key: str,
    artifacts: ArtifactPaths,
) -> NormalizedDocument | None:
    if not is_cache_fresh(source_path, artifacts):
        return None
    try:
        markdown = artifacts.markdown_path.read_text(encoding="utf-8")
        plain_text = artifacts.plain_text_path.read_text(encoding="utf-8")
        page_index = _read_page_index(artifacts.page_index_path)
        structured = json.loads(artifacts.structured_json_path.read_text(encoding="utf-8"))
        diagnostics = json.loads(artifacts.diagnostics_path.read_text(encoding="utf-8"))
        snapshot_paths = [Path(item) for item in diagnostics.get("snapshot_paths", []) if isinstance(item, str)]
        return NormalizedDocument(
            source_path=source_path,
            document_kind=document_kind,
            cache_key=cache_key,
            cache_hit=True,
            layout_fidelity=str(diagnostics.get("layout_fidelity", "degraded")),
            extractor_used=str(diagnostics.get("extractor_used", "unknown")),
            conversion_used=diagnostics.get("conversion_used"),
            warning=diagnostics.get("warning"),
            artifacts=artifacts,
            normalized_source_path=Path(str(diagnostics.get("normalized_source_path", artifacts.normalized_source_path))),
            markdown=markdown,
            plain_text=plain_text,
            page_index=page_index,
            structured_pages=list(structured.get("pages", [])),
            diagnostics=diagnostics,
            snapshot_paths=snapshot_paths,
            content_list=structured.get("content_list"),
        )
    except Exception:
        return None


def save_normalized_document(
    *,
    source_path: Path,
    document_kind: DocumentKind,
    cache_key: str,
    artifacts: ArtifactPaths,
    normalized_source_path: Path,
    markdown: str,
    plain_text: str,
    page_index: dict[int, list[str]],
    structured_pages: list[dict[str, Any]],
    diagnostics: dict[str, Any],
    snapshot_paths: list[Path],
    content_list: list[dict[str, Any]] | None,
    layout_fidelity: LayoutFidelity,
    extractor_used: str,
    conversion_used: str | None,
    warning: str | None,
) -> NormalizedDocument:
    artifacts.cache_dir.mkdir(parents=True, exist_ok=True)
    artifacts.snapshots_dir.mkdir(parents=True, exist_ok=True)
    actual_normalized_source_path = normalized_source_path

    source_copy = artifacts.source_copy_path
    if source_path.resolve() != source_copy.resolve():
        shutil.copy2(source_path, source_copy)
    elif not source_copy.exists():
        shutil.copy2(source_path, source_copy)

    if normalized_source_path.suffix.lower() == ".pdf":
        if normalized_source_path.resolve() != artifacts.normalized_source_path.resolve():
            shutil.copy2(normalized_source_path, artifacts.normalized_source_path)
        elif not artifacts.normalized_source_path.exists():
            shutil.copy2(normalized_source_path, artifacts.normalized_source_path)
        actual_normalized_source_path = artifacts.normalized_source_path
    else:
        artifacts.normalized_source_path.touch(exist_ok=True)

    _write_text(artifacts.markdown_path, markdown)
    _write_text(artifacts.plain_text_path, plain_text)
    _write_json(artifacts.page_index_path, page_index)
    _write_json(
        artifacts.structured_json_path,
        {"pages": structured_pages, "content_list": content_list},
    )

    diagnostics_payload = {
        **diagnostics,
        "source_path": str(source_path),
        "source_copy_path": str(artifacts.source_copy_path),
        "normalized_source_path": str(actual_normalized_source_path),
        "cache_normalized_pdf_path": str(artifacts.normalized_source_path),
        "artifact_paths": artifacts.as_dict(),
        "snapshot_paths": [str(path) for path in snapshot_paths],
        "layout_fidelity": layout_fidelity,
        "extractor_used": extractor_used,
        "conversion_used": conversion_used,
        "warning": warning,
    }
    _write_json(artifacts.diagnostics_path, diagnostics_payload)

    manifest = {
        "source_path": str(source_path),
        "source_size": source_path.stat().st_size,
        "source_mtime_ns": source_path.stat().st_mtime_ns,
        "source_suffix": source_path.suffix.lower(),
        "cache_key": cache_key,
        "document_kind": document_kind,
        "layout_fidelity": layout_fidelity,
        "extractor_used": extractor_used,
        "conversion_used": conversion_used,
        "warning": warning,
    }
    _write_json(artifacts.manifest_path, manifest)

    return NormalizedDocument(
        source_path=source_path,
        document_kind=document_kind,
        cache_key=cache_key,
        cache_hit=False,
        layout_fidelity=layout_fidelity,
        extractor_used=extractor_used,
        conversion_used=conversion_used,
        warning=warning,
        artifacts=artifacts,
        normalized_source_path=actual_normalized_source_path,
        markdown=markdown,
        plain_text=plain_text,
        page_index=page_index,
        structured_pages=structured_pages,
        diagnostics=diagnostics_payload,
        snapshot_paths=snapshot_paths,
        content_list=content_list,
    )


def detect_libreoffice_binary(preferred: str | None = None) -> str | None:
    candidates = []
    if preferred:
        candidates.append(preferred)
    env_bin = os.getenv("LIBREOFFICE_BIN") or os.getenv("SOFFICE_BIN")
    if env_bin:
        candidates.append(env_bin)
    candidates.extend(
        [
            r"C:\Program Files\LibreOffice\program\soffice.com",
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.com",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ]
    )
    candidates.extend(["soffice", "libreoffice", "soffice.com", "soffice.exe"])
    for candidate in candidates:
        resolved = shutil.which(candidate) if candidate else None
        if resolved:
            return resolved
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _decode_subprocess_stream(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    encodings: list[str] = []
    preferred = locale.getpreferredencoding(False)
    if preferred:
        encodings.append(preferred)
    encodings.extend(["utf-8", "utf-8-sig", "gb18030", "gbk", "cp936"])
    seen: set[str] = set()
    for encoding in encodings:
        key = encoding.lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    fallback = preferred or "utf-8"
    return raw.decode(fallback, errors="replace")


def _run_command_capture(
    command: list[str],
    *,
    timeout_seconds: int,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    completed = subprocess.run(
        command,
        capture_output=True,
        text=False,
        timeout=timeout_seconds,
        check=False,
        env=env,
    )
    stdout = _decode_subprocess_stream(completed.stdout)
    stderr = _decode_subprocess_stream(completed.stderr)
    return completed.returncode, stdout, stderr


def convert_office_to_pdf(
    source_path: Path,
    output_dir: Path,
    *,
    libreoffice_binary: str | None = None,
    timeout_seconds: int = 120,
) -> Path:
    binary = detect_libreoffice_binary(libreoffice_binary)
    if not binary:
        raise FileNotFoundError("LibreOffice binary not found")
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        binary,
        "--headless",
        "--convert-to",
        "pdf",
        "--outdir",
        str(output_dir),
        str(source_path),
    ]
    returncode, stdout, stderr = _run_command_capture(command, timeout_seconds=timeout_seconds)
    if returncode != 0:
        raise RuntimeError(
            "LibreOffice conversion failed: "
            f"returncode={returncode}, stdout={stdout.strip()}, stderr={stderr.strip()}"
        )
    candidate = output_dir / f"{source_path.stem}.pdf"
    if candidate.exists():
        return candidate
    pdf_candidates = sorted(output_dir.glob("*.pdf"), key=lambda item: item.stat().st_mtime, reverse=True)
    if pdf_candidates:
        return pdf_candidates[0]
    raise RuntimeError("LibreOffice completed but no PDF output was produced")


def convert_with_word_com(
    source_path: Path,
    output_path: Path,
    *,
    target_kind: Literal["pdf", "docx"],
    timeout_seconds: int = 120,
) -> Path:
    if os.name != "nt":
        raise RuntimeError("Word COM conversion is only supported on Windows")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    script = """
$src = $env:FUSION_WORD_SRC
$dst = $env:FUSION_WORD_DST
$target = $env:FUSION_WORD_TARGET
$word = $null
$doc = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $doc = $word.Documents.Open($src, $false, $true)
    if ($target -eq 'pdf') {
        $doc.ExportAsFixedFormat($dst, 17)
    } elseif ($target -eq 'docx') {
        $doc.SaveAs([ref]$dst, [ref]16)
    } else {
        throw "Unsupported Word COM target: $target"
    }
    $doc.Close()
    $word.Quit()
    if (!(Test-Path $dst)) {
        throw "Word COM conversion did not produce output."
    }
} catch {
    if ($doc -ne $null) {
        try { $doc.Close() } catch {}
    }
    if ($word -ne $null) {
        try { $word.Quit() } catch {}
    }
    Write-Error $_
    exit 1
}
""".strip()
    env = dict(os.environ)
    env.update(
        {
            "FUSION_WORD_SRC": str(source_path),
            "FUSION_WORD_DST": str(output_path),
            "FUSION_WORD_TARGET": target_kind,
        }
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=False,
        timeout=timeout_seconds,
        check=False,
        env=env,
    )
    stdout = _decode_subprocess_stream(completed.stdout)
    stderr = _decode_subprocess_stream(completed.stderr)
    if completed.returncode != 0 or not output_path.exists():
        raise RuntimeError(
            "Word COM conversion failed: "
            f"returncode={completed.returncode}, stdout={stdout.strip()}, stderr={stderr.strip()}"
        )
    return output_path


def extract_docx_text(source_path: Path) -> str:
    texts: list[str] = []
    with zipfile.ZipFile(source_path, "r") as archive:
        xml_names = [name for name in archive.namelist() if name.startswith("word/") and name.endswith(".xml")]
        preferred = ["word/document.xml"] + [name for name in xml_names if name != "word/document.xml"]
        for name in preferred:
            try:
                xml_root = ET.fromstring(archive.read(name))
            except Exception:
                continue
            texts.extend(_extract_docx_text_nodes(xml_root))
    return "\n".join(line for line in texts if line.strip()).strip()


def _extract_docx_text_nodes(root: ET.Element) -> list[str]:
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    lines: list[str] = []
    for paragraph in root.findall(".//w:p", ns):
        parts = [node.text or "" for node in paragraph.findall(".//w:t", ns)]
        text = "".join(parts).replace("\xa0", " ").strip()
        if text:
            lines.append(text)
    return lines


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _read_page_index(path: Path) -> dict[int, list[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    page_index: dict[int, list[str]] = {}
    if isinstance(payload, dict):
        for key, value in payload.items():
            try:
                page = int(key)
            except Exception:
                continue
            if isinstance(value, list):
                page_index[page] = [str(item) for item in value if str(item).strip()]
    return page_index


def build_page_index_from_pages(pages: list[list[str]]) -> dict[int, list[str]]:
    return {
        page_number: [line for line in page_lines if line.strip()]
        for page_number, page_lines in enumerate(pages, start=1)
        if any(line.strip() for line in page_lines)
    }


def build_page_index_from_content_list(content_list: list[dict[str, Any]] | None) -> dict[int, list[str]]:
    if not content_list:
        return {}
    pages: dict[int, list[str]] = {}
    for item in content_list:
        if not isinstance(item, dict):
            continue
        page_idx = item.get("page_idx")
        if not isinstance(page_idx, int):
            page_number = item.get("page_number")
            if isinstance(page_number, int):
                page_idx = page_number - 1
            else:
                continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        pages.setdefault(page_idx + 1, []).append(text)
    return pages


def build_markdown_from_page_index(page_index: dict[int, list[str]], *, title: str = "Document") -> str:
    lines = [f"# {title}", ""]
    for page in sorted(page_index):
        lines.append(f"## Page {page}")
        lines.extend(page_index[page])
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def build_plain_text_from_page_index(page_index: dict[int, list[str]]) -> str:
    lines: list[str] = []
    for page in sorted(page_index):
        lines.append(f"--- Page {page} ---")
        lines.extend(page_index[page])
    return "\n".join(lines).strip()


def make_page_record(
    *,
    page_number: int,
    text: str,
    blocks: list[dict[str, Any]] | None = None,
    images: int = 0,
    used_ocr: bool = False,
    scanned_candidate: bool = False,
    low_quality: bool = False,
) -> PageRecord:
    return PageRecord(
        page_number=page_number,
        text=text,
        blocks=blocks or [],
        images=images,
        used_ocr=used_ocr,
        scanned_candidate=scanned_candidate,
        low_quality=low_quality,
    )
