from __future__ import annotations

from typing import Any

from src.server.capabilities.filings.chunking import (
    build_chunk_payload,
    fetch_filing_by_accession,
    is_us_symbol,
    iter_ndjson_chunks,
    looks_a_share_symbol,
)
from src.server.runtime.models import RuntimeContext


class FilingsCapabilityService:
    def __init__(self, runtime: RuntimeContext):
        self._runtime = runtime

    @property
    def _service(self):
        return self._runtime.container.filings_service()

    @property
    def _provider_facade(self):
        return self._runtime.provider_facade

    async def fetch_periodic_sec_filings(self, **kwargs) -> list[dict[str, Any]]:
        return await self._service.fetch_periodic_sec_filings(**kwargs)

    async def fetch_event_sec_filings(self, **kwargs) -> list[dict[str, Any]]:
        return await self._service.fetch_event_sec_filings(**kwargs)

    async def fetch_ashare_filings(self, **kwargs) -> list[dict[str, Any]]:
        return await self._service.fetch_ashare_filings(**kwargs)

    async def process_document(self, *, doc_id: str, url: str, doc_type: str, ticker: str | None = None) -> dict[str, Any]:
        resolved = await self._provider_facade.resolve_ticker(ticker) if ticker else None
        return await self._service.process_document(doc_id=doc_id, url=url, doc_type=doc_type, ticker=resolved)

    async def get_filing_markdown(self, *, ticker: str, doc_id: str) -> dict[str, Any]:
        resolved = await self._provider_facade.resolve_ticker(ticker) if ticker else ticker
        return await self._service.get_filing_markdown(ticker=resolved, doc_id=doc_id)

    async def get_document_chunks(self, *, ticker: str, doc_id: str, items: list[str] | None = None) -> dict[str, Any]:
        if looks_a_share_symbol(ticker):
            return self._service._error_result(  # noqa: SLF001
                code="INVALID_ROUTE",
                message="get_document_chunks is US SEC-only; A-share ticker is not supported.",
                details={"ticker": ticker, "doc_id": doc_id, "items": items or []},
                retriable=False,
                suggested_reroute="Use A-share announcement/news tools for CN filings.",
            )
        if not is_us_symbol(ticker):
            return self._service._error_result(  # noqa: SLF001
                code="INVALID_ROUTE",
                message="get_document_chunks only supports US tickers.",
                details={"ticker": ticker, "doc_id": doc_id, "items": items or []},
                retriable=False,
                suggested_reroute="Provide a valid US ticker (e.g., AAPL, NASDAQ:NVDA).",
            )

        pure_symbol, accession_number, filing = fetch_filing_by_accession(ticker, doc_id)
        if filing is None:
            return self._service._no_data_result(  # noqa: SLF001
                reason=f"SEC filing not found: {accession_number} for {pure_symbol}",
                details={"ticker": ticker, "doc_id": doc_id, "items": items or []},
                suggested_reroute="Adjust accession/time range or fetch filing list first.",
            )

        return build_chunk_payload(filing=filing, ticker=pure_symbol, doc_id=accession_number, items=items)

    async def stream_document_chunks(self, *, ticker: str, doc_id: str, items: list[str] | None = None):
        if looks_a_share_symbol(ticker) or not is_us_symbol(ticker):
            async def _invalid():
                yield '{"type":"error","error":"get_document_chunks only supports US SEC filings."}\n'

            return _invalid()

        pure_symbol, _accession_number, filing = fetch_filing_by_accession(ticker, doc_id)
        if filing is None:
            async def _missing():
                yield '{"type":"error","error":"filing not found"}\n'

            return _missing()

        return iter_ndjson_chunks(filing=filing, ticker=pure_symbol, items=items)


def get_filings_capability_service(runtime: RuntimeContext) -> FilingsCapabilityService:
    return FilingsCapabilityService(runtime)
