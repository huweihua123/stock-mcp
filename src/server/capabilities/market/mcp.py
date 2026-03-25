from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP

from src.server.capabilities.market.service import get_market_capability_service
from src.server.domain.symbols.errors import SymbolResolutionError
from src.server.transports.mcp.artifacts import (
    create_artifact_envelope,
    create_artifact_response,
    create_mcp_error_result,
    create_symbol_error_response,
)
from src.server.runtime import get_runtime_context
from src.server.utils.logger import logger


def register_market_tools(mcp: FastMCP) -> None:
    runtime = get_runtime_context()
    service = get_market_capability_service(runtime)

    @mcp.tool(tags={"market-kline"})
    async def get_kline_data(
        symbol: str,
        period: str = "30d",
        interval: str = "1d",
        ctx: Context | None = None,
    ) -> Any:
        try:
            if ctx:
                await ctx.info(f"📈 获取K线: {symbol}", extra={"period": period, "interval": interval})
            result = await service.get_historical_prices(symbol, period, interval)
            artifact = create_artifact_envelope(
                variant="table",
                name=f"{symbol} K线数据",
                content=result,
                description=f"{symbol} {period} {interval} K线数据，共 {result['count']} 条",
                visible_to_llm=False,
                display_in_report=True,
            )
            return create_artifact_response(
                summary=f"已获取 {symbol} 的 {period} {interval} K线数据，共 {result['count']} 条。",
                artifact=artifact,
            )
        except SymbolResolutionError as exc:
            return create_symbol_error_response(exc, variant="table", name=f"{symbol} K线数据")
        except Exception as exc:
            logger.error(f"Capability MCP error in get_kline_data: {exc}")
            return create_mcp_error_result(str(exc))

    @mcp.tool(tags={"market-quote"})
    async def get_real_time_price(symbol: str, ctx: Context | None = None) -> Any:
        try:
            if ctx:
                await ctx.info(f"💹 获取实时价格: {symbol}")
            result = await service.get_real_time_price(symbol)
            if not result:
                return create_mcp_error_result(f"Price not found for {symbol}", error_code="NOT_FOUND")
            artifact = create_artifact_envelope(
                variant="real_time_price",
                name=f"{symbol} 实时报价",
                content=result,
                description=f"{symbol} 当前价格 {result.get('price')}",
            )
            return create_artifact_response(
                summary=f"{symbol} 最新价 {result.get('price')} {result.get('currency')}",
                artifact=artifact,
            )
        except SymbolResolutionError as exc:
            return create_symbol_error_response(exc, variant="real_time_price", name=f"{symbol} 实时报价")
        except Exception as exc:
            logger.error(f"Capability MCP error in get_real_time_price: {exc}")
            return create_mcp_error_result(str(exc))

    @mcp.tool(tags={"market-asset"})
    async def get_asset_info(symbol: str, ctx: Context | None = None) -> Any:
        try:
            if ctx:
                await ctx.info(f"🧾 获取资产信息: {symbol}")
            result = await service.get_asset_info(symbol)
            if not result:
                return create_mcp_error_result(f"Asset not found: {symbol}", error_code="NOT_FOUND")
            artifact = create_artifact_envelope(
                variant="asset_info",
                name=f"{symbol} 资产信息",
                content=result,
                description=f"{symbol} 资产详情",
            )
            return create_artifact_response(
                summary=f"已获取 {symbol} 的资产信息。",
                artifact=artifact,
            )
        except SymbolResolutionError as exc:
            return create_symbol_error_response(exc, variant="asset_info", name=f"{symbol} 资产信息")
        except Exception as exc:
            logger.error(f"Capability MCP error in get_asset_info: {exc}")
            return create_mcp_error_result(str(exc))
