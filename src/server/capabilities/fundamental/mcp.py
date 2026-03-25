from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP

from src.server.capabilities.fundamental.service import get_fundamental_capability_service
from src.server.domain.symbols.errors import SymbolResolutionError
from src.server.transports.mcp.artifacts import create_artifact_envelope, create_artifact_response, create_mcp_error_result, create_symbol_error_response
from src.server.runtime import get_runtime_context
from src.server.utils.logger import logger


def register_fundamental_tools(mcp: FastMCP) -> None:
    runtime = get_runtime_context()
    service = get_fundamental_capability_service(runtime)

    @mcp.tool(tags={"fundamental-report"})
    async def get_financial_reports(symbol: str, ctx: Context | None = None) -> Any:
        try:
            if ctx:
                await ctx.info(f"📘 获取财务报告: {symbol}")
            result = await service.get_financials(symbol)
            artifact = create_artifact_envelope(
                variant="financial_report",
                name=f"{symbol} 财务报告",
                content=result,
                description=f"{symbol} 财务数据",
                visible_to_llm=False,
                display_in_report=True,
            )
            return create_artifact_response(summary=f"已获取 {symbol} 的财务报告数据。", artifact=artifact)
        except SymbolResolutionError as exc:
            return create_symbol_error_response(exc, variant="financial_report", name=f"{symbol} 财务报告")
        except Exception as exc:
            logger.error(f"Capability MCP error in get_financial_reports: {exc}")
            return create_mcp_error_result(str(exc))

    @mcp.tool(tags={"fundamental-valuation"})
    async def get_valuation_metrics(symbol: str, days: int = 250) -> Any:
        try:
            result = await service.get_valuation_metrics(symbol, days=days)
            artifact = create_artifact_envelope(
                variant="valuation_metrics",
                name=f"{symbol} 估值指标",
                content=result,
                description=f"{symbol} 估值指标",
            )
            return create_artifact_response(summary=f"已获取 {symbol} 的估值指标。", artifact=artifact)
        except SymbolResolutionError as exc:
            return create_symbol_error_response(exc, variant="valuation_metrics", name=f"{symbol} 估值指标")
        except Exception as exc:
            logger.error(f"Capability MCP error in get_valuation_metrics: {exc}")
            return create_mcp_error_result(str(exc))

    @mcp.tool(tags={"fundamental-us-valuation"})
    async def get_us_valuation_metrics(symbol: str) -> Any:
        try:
            result = await service.get_us_valuation_metrics(symbol)
            artifact = create_artifact_envelope(
                variant="us_valuation",
                name=f"{symbol} 美股估值",
                content=result,
                description=f"{symbol} 美股估值指标",
            )
            return create_artifact_response(summary=f"已获取 {symbol} 的美股估值指标。", artifact=artifact)
        except SymbolResolutionError as exc:
            return create_symbol_error_response(exc, variant="us_valuation", name=f"{symbol} 美股估值")
        except Exception as exc:
            logger.error(f"Capability MCP error in get_us_valuation_metrics: {exc}")
            return create_mcp_error_result(str(exc))
