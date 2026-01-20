# src/server/mcp/tools/asset_tools.py
"""MCP tools for asset search and management.
Provides asset search, price queries, and asset information retrieval.
Returns structured data (JSON).
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
import asyncio

from fastmcp import FastMCP

from src.server.core.dependencies import Container
from src.server.domain.types import AssetSearchQuery, AssetType
from src.server.utils.logger import logger


# ============================================================
# 核心业务逻辑实现 (可被 MCP 和 FastAPI 共享)
# ============================================================

async def get_multiple_prices_impl(tickers: List[str]) -> Dict[str, Any]:
    """
    批量获取资产实时价格 (核心实现)
    
    Args:
        tickers: 资产代码列表 (格式: EXCHANGE:SYMBOL)
        
    Returns:
        Dict[ticker, price_data] - 价格数据字典
        
    Raises:
        Exception: 数据获取失败时抛出异常
    """
    manager = Container.adapter_manager()
    logger.info("Fetching multiple prices", count=len(tickers), tickers=tickers)
    
    prices = await manager.get_multiple_prices(tickers)
    result = {}
    for ticker, price in prices.items():
        if price:
            result[ticker] = price.to_dict()
        else:
            result[ticker] = None
    
    success_count = len([v for v in result.values() if v])
    logger.info("Successfully fetched prices", success_count=success_count, total=len(tickers))
    return result


# ============================================================
# MCP 工具注册 (保持原有接口不变)
# ============================================================

def register_asset_tools(mcp: FastMCP):
    """Register asset-related tools."""

    @mcp.tool(tags={"asset-search", "asset-extended"})
    async def search_assets(
        query: str, asset_types: list[str] = None, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Search for assets (stocks, ETFs, crypto, etc.).

        Args:
            query: Search keyword (ticker or name)
            asset_types: List of asset types (stock, etf, crypto, index)
            limit: Max results (default 10)

        Returns:
            List of asset search results
        """
        try:
            manager = Container.adapter_manager()
            logger.info("MCP tool called: search_assets", query=query, limit=limit)

            types = []
            if asset_types:
                for t in asset_types:
                    try:
                        types.append(AssetType(t.lower()))
                    except ValueError:
                        pass

            search_query = AssetSearchQuery(
                query=query, asset_types=types if types else None, limit=limit
            )

            results = await manager.search_assets(search_query)
            return [r.model_dump(mode="json") for r in results]

        except Exception as e:
            logger.error(f"Asset search failed: {e}")
            return [{"error": str(e)}]

    @mcp.tool(tags={"asset-info", "asset-extended"})
    async def get_asset_info(ticker: str) -> Dict[str, Any]:
        """Get detailed asset information.

        Args:
            ticker: Asset ticker. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA
                - 加密货币: CRYPTO:BTC, CRYPTO:ETH

        Returns:
            Asset details
        """
        try:
            manager = Container.adapter_manager()
            logger.info("MCP tool called: get_asset_info", ticker=ticker)

            asset = await manager.get_asset_info(ticker)
            if asset:
                return asset.model_dump(mode="json")
            return {"error": f"Asset not found: {ticker}"}

        except Exception as e:
            logger.error(f"Get asset info failed: {e}")
            return {"error": str(e)}

    @mcp.tool(tags={"asset-price", "asset-extended"})
    async def get_real_time_price(ticker: str) -> Dict[str, Any]:
        """Get real-time price for an asset.

        Args:
            ticker: Asset ticker. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA
                - 加密货币: CRYPTO:BTC, CRYPTO:ETH

        Returns:
            Real-time price data
        """
        try:
            manager = Container.adapter_manager()
            logger.info("MCP tool called: get_real_time_price", ticker=ticker)

            price = await manager.get_real_time_price(ticker)
            if price:
                return price.to_dict()
            return {"error": f"Price not found for {ticker}"}

        except Exception as e:
            logger.error(f"Get real-time price failed: {e}")
            return {"error": str(e)}

    @mcp.tool(tags={"asset-price-batch", "asset-extended"})
    async def get_multiple_prices(tickers: list[str]) -> Dict[str, Any]:
        """Get real-time prices for multiple assets.

        Args:
            tickers: List of asset tickers. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA
                - 加密货币: CRYPTO:BTC, CRYPTO:ETH

        Returns:
            Dictionary mapping tickers to price data
        """
        try:
            return await get_multiple_prices_impl(tickers)
        except Exception as e:
            logger.error(f"MCP tool error in get_multiple_prices: {e}", exc_info=True)
            return {"error": str(e)}

    @mcp.tool(tags={"asset-history", "asset-extended"})
    async def get_historical_prices(
        ticker: str, start_date: str, end_date: str, interval: str = "1d"
    ) -> List[Dict[str, Any]]:
        """Get historical price data.

        Args:
            ticker: Asset ticker. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA
                - 加密货币: CRYPTO:BTC, CRYPTO:ETH
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            interval: Data interval (1d, 1wk, 1mo)

        Returns:
            List of historical price data points
        """
        try:
            manager = Container.adapter_manager()
            logger.info(
                "MCP tool called: get_historical_prices",
                ticker=ticker,
                start=start_date,
                end=end_date,
            )

            start = datetime.strptime(start_date, "%Y-%m-%d")
            end = datetime.strptime(end_date, "%Y-%m-%d")

            prices = await manager.get_historical_prices(
                ticker=ticker,
                start_date=start,
                end_date=end,
                interval=interval,
            )
            return [p.to_dict() for p in prices]

        except Exception as e:
            logger.error(f"Get historical prices failed: {e}")
            return [{"error": str(e)}]

    @mcp.tool(tags={"market-report", "asset-extended"})
    async def get_market_report(symbol: str) -> Dict[str, Any]:
        """Get a comprehensive market report for the given ticker.
        Includes current price and asset info.

        Args:
            symbol: Asset ticker. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA
                - 加密货币: CRYPTO:BTC, CRYPTO:ETH

        Returns:
            Dictionary with asset info and current price
        """
        try:
            manager = Container.adapter_manager()
            logger.info("MCP tool called: get_market_report", symbol=symbol)

            # Fetch info and price in parallel
            info, price = await asyncio.gather(
                manager.get_asset_info(symbol), manager.get_real_time_price(symbol)
            )

            return {
                "symbol": symbol,
                "info": info.model_dump(mode="json") if info else None,
                "price": price.to_dict() if price else None,
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            logger.error(f"Failed to get market report: {e}")
            return {"error": str(e)}
