# src/server/mcp/tools/asset_tools.py
"""MCP tools for asset search and management.
Provides asset search, price queries, and asset information retrieval.
Returns structured data (JSON).

Active Tools (1):
  - get_kline_data: 获取K线历史价格数据

Disabled Tools:
  - get_asset_info: 🔇 当前策略仅保留K线原子能力
  - get_real_time_price: 🔇 由策略层改用 get_kline_data 派生最新价
  - get_multiple_prices: 🔇 当前策略仅保留K线原子能力
  - get_market_report: 🔇 聚合工具，应由 Agent 层调用原子工具组合
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP, Context

from src.server.core.use_cases import market as market_use_cases
from src.server.domain.types import AssetType
from src.server.utils.logger import logger
from src.server.mcp.tools.artifact_utils import (
    create_artifact_envelope,
    create_artifact_response,
    create_symbol_error_response,
)
from src.server.domain.symbols.errors import SymbolResolutionError


# ============================================================
# MCP 工具注册 (保持原有接口不变)
# ============================================================


def register_asset_tools(mcp: FastMCP):
    """Register asset-related tools."""


    async def get_asset_info(ticker: str, ctx: Context = None) -> Dict[str, Any]:
        """Get detailed asset information.

        Args:
            ticker: Asset ticker. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA
                - 加密货币: CRYPTO:BTC, CRYPTO:ETH
            ctx: FastMCP Context for logging

        Returns:
            Asset details
        """
        if ctx:
            await ctx.info(f"🔧 获取资产信息: {ticker}", extra={"ticker": ticker})

        try:
            logger.info("MCP tool called: get_asset_info", ticker=ticker)
            asset = await market_use_cases.get_asset_info(ticker)
            if asset:
                result = asset
                result["component_type"] = "asset_info"
                
                if ctx:
                    await ctx.info(f"✅ 获取资产信息完成: {ticker}")
                
                # 构造 Artifact
                artifact = create_artifact_envelope(
                    component_type="asset_info",
                    name=f"{ticker} 资产信息",
                    content=result,
                    description=f"{result.get('name')} ({ticker}) 基本信息",
                )
                
                # 构造 Summary
                summary = (
                    f"已获取 {result.get('name')} ({ticker}) 的基本信息。\\n"
                    f"行业：{result.get('industry')}\\n"
                    f"市值：{result.get('market_cap')}"
                )
                
                return create_artifact_response(summary=summary, artifact=artifact)
            
            if ctx:
                await ctx.warning(f"⚠️ 未找到资产信息: {ticker}")

            return {
                "error": f"Asset not found: {ticker}",
                "component_type": "asset_info",
            }

        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败: {ticker}", extra=e.to_dict())
            return create_symbol_error_response(
                e, component_type="asset_info", name=f"{ticker} 资产信息"
            )
        except Exception as e:
            logger.error(f"Get asset info failed: {e}")
            if ctx:
                await ctx.error(
                    f"❌ 获取资产信息失败: {ticker}",
                    extra={"error": str(e)}
                )
            return {"error": str(e), "component_type": "asset_info"}

    async def get_real_time_price(ticker: str, ctx: Context = None) -> Dict[str, Any]:
        """Get real-time price for an asset.

        Args:
            ticker: Asset ticker. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA
                - 加密货币: CRYPTO:BTC, CRYPTO:ETH
            ctx: FastMCP Context for logging

        Returns:
            Real-time price data
        """
        if ctx:
            await ctx.info(f"🔧 获取实时价格: {ticker}", extra={"ticker": ticker})

        try:
            logger.info("MCP tool called: get_real_time_price", ticker=ticker)
            price = await market_use_cases.get_real_time_price(ticker)
            if price:
                result = price
                result["component_type"] = "real_time_price"
                
                if ctx:
                    await ctx.info(
                        f"✅ 获取实时价格完成: {ticker}",
                        extra={"price": result.get("price")}
                    )

                # 构造 Artifact
                artifact = create_artifact_envelope(
                    component_type="real_time_price",
                    name=f"{ticker} 实时报价",
                    content=result,
                    description=(
                        f"{ticker} 当前价格: {result.get('price')} {result.get('currency')}"
                    ),
                )
                
                # 构造 Summary
                summary = (
                    f"{ticker} 最新价 {result.get('price')} {result.get('currency')}，"
                    f"涨跌幅 {result.get('change_percent')}%"
                )

                return create_artifact_response(summary=summary, artifact=artifact)
            
            if ctx:
                await ctx.warning(f"⚠️ 未找到实时价格: {ticker}")

            return {
                "error": f"Price not found for {ticker}",
                "component_type": "real_time_price",
            }

        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败: {ticker}", extra=e.to_dict())
            return create_symbol_error_response(
                e, component_type="real_time_price", name=f"{ticker} 实时报价"
            )
        except Exception as e:
            logger.error(f"Get real-time price failed: {e}")
            if ctx:
                await ctx.error(
                    f"❌ 获取实时价格失败: {ticker}",
                    extra={"error": str(e)}
                )
            return {"error": str(e), "component_type": "real_time_price"}

    async def get_multiple_prices(tickers: list[str], ctx: Context = None) -> Dict[str, Any]:
        """Get real-time prices for multiple assets.

        Args:
            tickers: List of asset tickers. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA
                - 加密货币: CRYPTO:BTC, CRYPTO:ETH
            ctx: FastMCP Context for logging

        Returns:
            Dictionary mapping tickers to price data
        """
        if ctx:
            await ctx.info(
                f"🔧 批量获取价格: {len(tickers)}个资产",
                extra={"tickers": tickers}
            )

        try:
            result = await market_use_cases.get_multiple_prices(tickers)
            result["component_type"] = "multiple_prices"
            
            if ctx:
                await ctx.info(f"✅ 批量获取价格完成: {len(result)}个结果")

            # 构造 Artifact
            artifact = create_artifact_envelope(
                component_type="multiple_prices",
                name="批量实时报价",
                content=result,
                description=f"包含 {len(tickers)} 个资产的实时价格",
            )
            
            # 构造 Summary
            summary = f"已获取 {len(tickers)} 个资产的实时价格。"

            return create_artifact_response(summary=summary, artifact=artifact)

        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败", extra=e.to_dict())
            return create_symbol_error_response(
                e, component_type="multiple_prices", name="批量实时报价"
            )
        except Exception as e:
            logger.error(f"MCP tool error in get_multiple_prices: {e}", exc_info=True)
            if ctx:
                await ctx.error(
                    f"❌ 批量获取价格失败",
                    extra={"error": str(e)}
                )
            return {"error": str(e), "component_type": "multiple_prices"}

    @mcp.tool(tags={"asset"})
    async def get_kline_data(
        ticker: str, start_date: str, end_date: str, interval: str = "1d", ctx: Context = None) -> Dict[str, Any]:
        """Get K-line historical price data.

        Args:
            ticker: Asset ticker. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA
                - 加密货币: CRYPTO:BTC, CRYPTO:ETH
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            interval: Data interval (1d, 1wk, 1mo)
            ctx: FastMCP Context for logging

        Returns:
            Dictionary containing historical price data list
        """
        if ctx:
            await ctx.info(
                f"🔧 获取历史价格: {ticker}",
                extra={"ticker": ticker, "start": start_date, "end": end_date, "interval": interval}
            )

        try:
            logger.info(
                "MCP tool called: get_kline_data",
                ticker=ticker,
                start=start_date,
                end=end_date,
            )

            start = datetime.strptime(start_date, "%Y-%m-%d")
            end = datetime.strptime(end_date, "%Y-%m-%d")

            prices = await market_use_cases.get_historical_prices(
                ticker=ticker, start_date=start, end_date=end, interval=interval
            )
            
            if ctx:
                await ctx.info(
                    f"✅ 获取历史价格完成: {ticker}",
                    extra={"count": len(prices)}
                )

            result = {
                "component_type": "price_chart",
                "symbol": ticker,
                "data": prices,
            }

            description = f"{ticker}历史价格: {start_date}至{end_date}, 共{len(prices)}条数据"

            artifact = create_artifact_envelope(
                component_type="price_chart",
                name=f"{ticker} 历史价格",
                content=result,
                description=description,
                visible_to_llm=False,
                display_in_report=True,
            )
            
            return create_artifact_response(summary=description, artifact=artifact)

        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败: {ticker}", extra=e.to_dict())
            return create_symbol_error_response(
                e, component_type="price_chart", name=f"{ticker} 历史价格"
            )
        except Exception as e:
            logger.error(f"Get historical prices failed: {e}")
            if ctx:
                await ctx.error(
                    f"❌ 获取历史价格失败: {ticker}",
                    extra={"error": str(e)}
                )
            return {"error": str(e), "component_type": "kline_chart"}
