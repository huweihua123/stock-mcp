from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP

from src.server.capabilities.money_flow.service import get_money_flow_capability_service
from src.server.domain.symbols.errors import SymbolResolutionError
from src.server.transports.mcp.artifacts import create_artifact_envelope, create_artifact_response, create_mcp_error_result, create_symbol_error_response
from src.server.runtime import get_runtime_context
from src.server.utils.logger import logger


def register_money_flow_tools(mcp: FastMCP) -> None:
    runtime = get_runtime_context()
    service = get_money_flow_capability_service(runtime)

    @mcp.tool(tags={"money-flow-stock"})
    async def get_money_flow(symbol: str, days: int = 20, ctx: Context | None = None) -> Any:
        try:
            if ctx:
                await ctx.info(f"💰 获取资金流向: {symbol}", extra={"days": days})
            result = await service.get_money_flow(symbol, days)
            artifact = create_artifact_envelope(
                variant="money_flow",
                name=f"{symbol} 资金流向",
                content=result,
                description=f"{symbol} 最近 {days} 天资金流向",
            )
            return create_artifact_response(summary=f"已获取 {symbol} 最近 {days} 天资金流向。", artifact=artifact)
        except SymbolResolutionError as exc:
            return create_symbol_error_response(exc, variant="money_flow", name=f"{symbol} 资金流向")
        except Exception as exc:
            logger.error(f"Capability MCP error in get_money_flow: {exc}")
            return create_mcp_error_result(str(exc))

    @mcp.tool(tags={"money-flow-north-bound"})
    async def get_north_bound_flow(days: int = 30) -> Any:
        try:
            result = await service.get_north_bound_flow(days)
            artifact = create_artifact_envelope(
                variant="north_bound_flow",
                name="北向资金流向",
                content=result,
                description=f"最近 {days} 天北向资金流向",
            )
            return create_artifact_response(summary=f"已获取最近 {days} 天北向资金流向。", artifact=artifact)
        except Exception as exc:
            logger.error(f"Capability MCP error in get_north_bound_flow: {exc}")
            return create_mcp_error_result(str(exc))

    @mcp.tool(tags={"money-flow-chip"})
    async def get_chip_distribution(symbol: str, days: int = 30) -> Any:
        try:
            result = await service.get_chip_distribution(symbol, days)
            artifact = create_artifact_envelope(
                variant="chip_distribution",
                name=f"{symbol} 筹码分布",
                content=result,
                description=f"{symbol} 最近 {days} 天筹码分布",
            )
            return create_artifact_response(summary=f"已获取 {symbol} 最近 {days} 天筹码分布。", artifact=artifact)
        except SymbolResolutionError as exc:
            return create_symbol_error_response(exc, variant="chip_distribution", name=f"{symbol} 筹码分布")
        except Exception as exc:
            logger.error(f"Capability MCP error in get_chip_distribution: {exc}")
            return create_mcp_error_result(str(exc))
