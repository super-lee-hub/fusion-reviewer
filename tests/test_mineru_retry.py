from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from deepreview.adapters.mineru import MineruAdapter, MineruConfig, MineruParseResult


def test_mineru_retries_transient_transport_error(tmp_path, monkeypatch):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%fake\n")

    adapter = MineruAdapter(
        MineruConfig(
            base_url="https://mineru.net/api/v4",
            api_token="token",
            model_version="vlm",
            upload_endpoint="/file-urls/batch",
            poll_endpoint_templates=["/extract-results/batch/{batch_id}"],
            poll_interval_seconds=0.01,
            poll_timeout_seconds=30,
            allow_local_fallback=False,
            request_max_retries=2,
            retry_backoff_seconds=0.0,
        )
    )

    attempts = {"count": 0}

    async def fake_parse_via_mineru(*, pdf_path: Path, pdf_bytes: bytes, data_id: str) -> MineruParseResult:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return MineruParseResult(
            markdown="# parsed",
            content_list=[],
            batch_id="batch-1",
            raw_result={"status": "done"},
            provider="mineru_v4",
        )

    monkeypatch.setattr(adapter, "_parse_via_mineru", fake_parse_via_mineru)

    result = asyncio.run(adapter.parse_pdf(pdf_path=pdf_path, data_id="paper"))

    assert attempts["count"] == 2
    assert result.markdown == "# parsed"
    assert result.warning == "MinerU succeeded after 1 retry(s)."
