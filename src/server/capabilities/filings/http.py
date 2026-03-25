from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from fastapi.responses import StreamingResponse

from src.server.capabilities.filings.schemas import ProcessDocumentRequest
from src.server.capabilities.filings.service import get_filings_capability_service
from src.server.runtime.models import RuntimeContext


def build_router(runtime: RuntimeContext) -> APIRouter:
    router = APIRouter(prefix="/api/v1/filings", tags=["Filings"])
    service = get_filings_capability_service(runtime)

    @router.get("/sec/periodic")
    async def get_periodic_sec_filings(
        ticker: str,
        year: Optional[int] = None,
        quarter: Optional[int] = None,
        forms: Optional[List[str]] = Query(None),
        limit: int = 10,
    ):
        try:
            return await service.fetch_periodic_sec_filings(
                ticker=ticker, forms=forms, year=year, quarter=quarter, limit=limit
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/sec/event")
    async def get_event_sec_filings(
        ticker: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        forms: Optional[List[str]] = Query(None),
        limit: int = 10,
    ):
        try:
            return await service.fetch_event_sec_filings(
                ticker=ticker, forms=forms, start_date=start_date, end_date=end_date, limit=limit
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/ashare")
    async def get_ashare_filings(
        symbol: str,
        filing_types: Optional[List[str]] = Query(None),
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 10,
    ):
        try:
            return await service.fetch_ashare_filings(
                symbol=symbol, filing_types=filing_types, start_date=start_date, end_date=end_date, limit=limit
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/process")
    async def process_document(request: ProcessDocumentRequest):
        try:
            result = await service.process_document(
                doc_id=request.doc_id,
                url=request.url,
                doc_type=request.doc_type,
                ticker=request.ticker,
            )
            if result.get("status") in {"failed", "error"}:
                raise HTTPException(status_code=500, detail=result.get("error", "Unknown error"))
            return result
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/markdown")
    async def get_filing_markdown(ticker: str, doc_id: str, stream: bool = False):
        try:
            result = await service.get_filing_markdown(ticker=ticker, doc_id=doc_id)
            if result.get("status") == "error":
                raise HTTPException(
                    status_code=404 if "not found" in result.get("error", "").lower() else 500,
                    detail=result.get("error", "Unknown error"),
                )
            if stream:
                return PlainTextResponse(
                    content=result.get("content", ""),
                    media_type="text/markdown",
                    headers={
                        "X-Cached": str(result.get("cached", False)),
                        "X-Doc-Id": doc_id,
                        "X-Ticker": ticker,
                    },
                )
            return result
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/chunks")
    async def get_document_chunks(
        ticker: str,
        doc_id: str,
        items: Optional[List[str]] = Query(None),
        stream: bool = False,
    ):
        try:
            if stream:
                generator = await service.stream_document_chunks(ticker=ticker, doc_id=doc_id, items=items)
                return StreamingResponse(
                    generator,
                    media_type="application/x-ndjson",
                    headers={
                        "X-Content-Type-Options": "nosniff",
                        "Cache-Control": "no-cache",
                    },
                )
            result = await service.get_document_chunks(ticker=ticker, doc_id=doc_id, items=items)
            if result.get("status") == "error":
                raise HTTPException(status_code=400, detail=result.get("message", "failed to get chunks"))
            if result.get("status") == "no_data":
                raise HTTPException(status_code=404, detail=result.get("message", "filing not found"))
            return result
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return router
