from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.server.capabilities.filings.service import FilingsCapabilityService


def _runtime():
    container = SimpleNamespace(
        filings_service=lambda: SimpleNamespace(
            _error_result=lambda **kwargs: {"status": "error", **kwargs},
            _no_data_result=lambda **kwargs: {"status": "no_data", **kwargs},
        ),
        market_gateway=lambda: SimpleNamespace(resolve_ticker=None),
    )
    return SimpleNamespace(container=container)


def test_filings_capability_service_rejects_a_share_for_chunks():
    service = FilingsCapabilityService(_runtime())

    result = asyncio.run(service.get_document_chunks(ticker="600519.SH", doc_id="SEC:demo"))

    assert result["status"] == "error"
    assert result["code"] == "INVALID_ROUTE"


def test_filings_capability_service_builds_chunk_payload(monkeypatch):
    service = FilingsCapabilityService(_runtime())

    fake_filing = SimpleNamespace(form="10-K", filing_date="2026-03-25")
    monkeypatch.setattr(
        "src.server.capabilities.filings.service.fetch_filing_by_accession",
        lambda ticker, doc_id: ("AAPL", "0001", fake_filing),
    )
    monkeypatch.setattr(
        "src.server.capabilities.filings.service.build_chunk_payload",
        lambda filing, ticker, doc_id, items=None: {
            "status": "ok",
            "ticker": ticker,
            "doc_id": doc_id,
            "chunks_count": 2,
            "chunks": [{"type": "chunk"}, {"type": "chunk"}],
        },
    )

    result = asyncio.run(service.get_document_chunks(ticker="AAPL", doc_id="SEC:0001", items=["Item 1A"]))

    assert result["status"] == "ok"
    assert result["ticker"] == "AAPL"
    assert result["chunks_count"] == 2
