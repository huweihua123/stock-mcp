from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from src.server.capabilities.filings.service import get_filings_capability_service
from src.server.domain.symbols.errors import SymbolResolutionError
from src.server.transports.mcp.artifacts import create_artifact_envelope, create_artifact_response, create_mcp_error_result, create_symbol_error_response
from src.server.runtime import get_runtime_context
from src.server.utils.logger import logger


def register_filings_tools(mcp: FastMCP) -> None:
    runtime = get_runtime_context()
    service = get_filings_capability_service(runtime)

    @mcp.tool(tags={"filings-sec-periodic"})
    async def fetch_periodic_sec_filings(ticker: str, year: int | None = None, quarter: int | None = None, forms: list[str] | None = None, limit: int = 10) -> Any:
        try:
            results = await service.fetch_periodic_sec_filings(ticker=ticker, forms=forms, year=year, quarter=quarter, limit=limit)
            artifact = create_artifact_envelope(
                variant="research_reports",
                name=f"{ticker} SEC 定期报告",
                content={"items": results},
                description=f"{ticker} SEC 定期报告，共 {len(results)} 条",
            )
            return create_artifact_response(summary=f"已获取 {ticker} 的 SEC 定期报告，共 {len(results)} 条。", artifact=artifact)
        except SymbolResolutionError as exc:
            return create_symbol_error_response(exc, variant="research_reports", name=f"{ticker} SEC 定期报告")
        except Exception as exc:
            logger.error(f"Capability MCP error in fetch_periodic_sec_filings: {exc}")
            return create_mcp_error_result(str(exc))

    @mcp.tool(tags={"filings-sec-event"})
    async def fetch_event_sec_filings(ticker: str, start_date: str | None = None, end_date: str | None = None, forms: list[str] | None = None, limit: int = 10) -> Any:
        try:
            results = await service.fetch_event_sec_filings(ticker=ticker, forms=forms, start_date=start_date, end_date=end_date, limit=limit)
            artifact = create_artifact_envelope(
                variant="research_reports",
                name=f"{ticker} SEC 事件报告",
                content={"items": results},
                description=f"{ticker} SEC 事件报告，共 {len(results)} 条",
            )
            return create_artifact_response(summary=f"已获取 {ticker} 的 SEC 事件报告，共 {len(results)} 条。", artifact=artifact)
        except SymbolResolutionError as exc:
            return create_symbol_error_response(exc, variant="research_reports", name=f"{ticker} SEC 事件报告")
        except Exception as exc:
            logger.error(f"Capability MCP error in fetch_event_sec_filings: {exc}")
            return create_mcp_error_result(str(exc))

    @mcp.tool(tags={"filings-ashare"})
    async def fetch_ashare_filings(symbol: str, filing_types: list[str] | None = None, start_date: str | None = None, end_date: str | None = None, limit: int = 10) -> Any:
        try:
            results = await service.fetch_ashare_filings(symbol=symbol, filing_types=filing_types, start_date=start_date, end_date=end_date, limit=limit)
            artifact = create_artifact_envelope(
                variant="research_reports",
                name=f"{symbol} A股公告",
                content={"items": results},
                description=f"{symbol} A股公告，共 {len(results)} 条",
            )
            return create_artifact_response(summary=f"已获取 {symbol} 的 A股公告，共 {len(results)} 条。", artifact=artifact)
        except SymbolResolutionError as exc:
            return create_symbol_error_response(exc, variant="research_reports", name=f"{symbol} A股公告")
        except Exception as exc:
            logger.error(f"Capability MCP error in fetch_ashare_filings: {exc}")
            return create_mcp_error_result(str(exc))

    @mcp.tool(tags={"filings-markdown"})
    async def get_filing_markdown(ticker: str, doc_id: str) -> Any:
        try:
            result = await service.get_filing_markdown(ticker=ticker, doc_id=doc_id)
            artifact = create_artifact_envelope(
                variant="research_reports",
                name=f"{ticker} {doc_id} Markdown",
                content=result,
                description=f"{ticker} {doc_id} Markdown 内容",
            )
            return create_artifact_response(summary=f"已获取 {ticker} {doc_id} 的 Markdown 内容。", artifact=artifact)
        except SymbolResolutionError as exc:
            return create_symbol_error_response(exc, variant="research_reports", name=f"{ticker} Filing Markdown")
        except Exception as exc:
            logger.error(f"Capability MCP error in get_filing_markdown: {exc}")
            return create_mcp_error_result(str(exc))

    @mcp.tool(tags={"filings-chunks"})
    async def get_document_chunks(ticker: str, doc_id: str, items: list[str] | None = None) -> Any:
        try:
            result = await service.get_document_chunks(ticker=ticker, doc_id=doc_id, items=items)
            if result.get("status") == "error":
                return create_mcp_error_result(
                    result.get("message", "failed to get filing chunks"),
                    error_code=str(result.get("error", {}).get("code", "INTERNAL_ERROR")),
                    details=result,
                )
            if result.get("status") == "no_data":
                return create_mcp_error_result(
                    result.get("message", "no filing chunks found"),
                    error_code="NO_DATA",
                    details=result,
                )
            artifact = create_artifact_envelope(
                variant="research_reports",
                name=f"{ticker} {doc_id} Chunks",
                content=result,
                description=f"{ticker} {doc_id} filing chunks，共 {result.get('chunks_count', 0)} 条",
                display_in_report=False,
            )
            return create_artifact_response(
                summary=f"已获取 {ticker} {doc_id} 的文档分块，共 {result.get('chunks_count', 0)} 条。",
                artifact=artifact,
            )
        except SymbolResolutionError as exc:
            return create_symbol_error_response(exc, variant="research_reports", name=f"{ticker} Filing Chunks")
        except Exception as exc:
            logger.error(f"Capability MCP error in get_document_chunks: {exc}")
            return create_mcp_error_result(str(exc))
