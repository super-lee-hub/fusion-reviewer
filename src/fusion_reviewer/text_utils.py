"""Shared text decoding and quality checks.

Centralises duplicated private functions from service.py, codex_runtime.py,
and codex_repair.py into a single importable location.
"""

from pathlib import Path


def decode_text_file(path: Path) -> str:
    """Decode a text file with multi-encoding fallback."""
    raw_bytes = path.read_bytes()
    return decode_text_bytes(raw_bytes)


def decode_text_bytes(raw_bytes: bytes) -> str:
    """Decode raw bytes with multi-encoding fallback."""
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="ignore")


def looks_garbled(text: str) -> bool:
    """Check if text appears shell-corrupted or encoding-damaged."""
    if "\ufffd" in text:
        return True
    stripped = text.strip()
    if not stripped:
        return False
    return stripped.count("?") > max(30, len(stripped) // 25)
