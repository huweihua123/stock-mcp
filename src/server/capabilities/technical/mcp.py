from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP

from src.server.transports.mcp.artifacts import create_artifact_envelope, create_artifact_response, create_mcp_error_result
from src.server.runtime import get_runtime_context
from src.server.utils.logger import logger
from src.server.capabilities.technical.service import get_technical_capability_service


def register_technical_tools(mcp: FastMCP) -> None:
    runtime = get_runtime_context()
    service = get_technical_capability_service(runtime)

    @mcp.tool(tags={"technical-indicators"})
    async def get_technical_indicators(
        symbol: str,
        period: str = "30d",
        interval: str = "1d",
        ctx: Context | None = None,
    ) -> Any:
        try:
            if ctx:
                await ctx.info(f"📊 计算技术指标: {symbol}", extra={"period": period, "interval": interval})
            result = await service.calculate_technical_indicators(symbol, period, interval)
            artifact = create_artifact_envelope(
                variant="technical_indicators",
                name=f"{symbol} 技术指标",
                content=result,
                description=f"{symbol} 技术指标结果",
                visible_to_llm=False,
                display_in_report=True,
            )
            return create_artifact_response(summary=f"已计算 {symbol} 的技术指标。", artifact=artifact)
        except Exception as exc:
            logger.error(f"Capability MCP error in get_technical_indicators: {exc}")
            return create_mcp_error_result(str(exc))

    @mcp.tool(tags={"technical-signal"})
    async def generate_trading_signal(symbol: str, period: str = "30d", interval: str = "1d") -> Any:
        try:
            result = await service.generate_trading_signal(symbol, period, interval)
            artifact = create_artifact_envelope(
                variant="technical_signal",
                name=f"{symbol} 交易信号",
                content=result,
                description=f"{symbol} 交易信号",
            )
            return create_artifact_response(summary=f"已生成 {symbol} 的交易信号。", artifact=artifact)
        except Exception as exc:
            logger.error(f"Capability MCP error in generate_trading_signal: {exc}")
            return create_mcp_error_result(str(exc))

    @mcp.tool(tags={"technical-support"})
    async def calculate_support_resistance(symbol: str, period: str = "90d") -> Any:
        try:
            result = await service.calculate_support_resistance(symbol, period)
            artifact = create_artifact_envelope(
                variant="technical_levels",
                name=f"{symbol} 支撑阻力位",
                content=result,
                description=f"{symbol} 支撑阻力位分析",
            )
            return create_artifact_response(summary=f"已计算 {symbol} 的支撑阻力位。", artifact=artifact)
        except Exception as exc:
            logger.error(f"Capability MCP error in calculate_support_resistance: {exc}")
            return create_mcp_error_result(str(exc))
