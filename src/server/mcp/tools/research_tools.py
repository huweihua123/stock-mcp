"""
Author: weihua hu
Date: 2025-11-25 01:47:01
LastEditTime: 2025-11-25 01:52:18
LastEditors: weihua hu
Description:
"""

# src/server/mcp/tools/research_tools.py
"""MCP tool for deep research.
Combines market data, fundamental data, and recent news into a structured
report. Returns structured data (JSON).
"""

from datetime import datetime, timedelta
from typing import Any, Dict

from nacos_mcp_wrapper.server.nacos_mcp import NacosMCP

from src.server.core.dependencies import Container
from src.server.utils.logger import logger


def register_research_tools(mcp: NacosMCP):
    """Register research tools for Nacos MCP.

    Args:
        mcp: NacosMCP instance
    """

    @mcp.tool()
    async def perform_deep_research(symbol: str, days_back: int = 30) -> Dict[str, Any]:
        """Generate a deep research report for `symbol`.

        Aggregates:
        1. Market Data (Price & History)
        2. Fundamental Analysis
        3. Recent News

        Args:
            symbol: Stock symbol
            days_back: News lookback days

        Returns:
            Dictionary containing aggregated research data
        """
        logger.info(
            "MCP tool: perform_deep_research", symbol=symbol, days_back=days_back
        )

        manager = Container.adapter_manager()
        fundamental_srv = Container.fundamental_service()
        news_srv = Container.news_service()

        async def get_market_data():
            try:
                end = datetime.now()
                start = end - timedelta(days=365)

                price = await manager.get_real_time_price(symbol)
                history = await manager.get_historical_prices(symbol, start, end)
                info = await manager.get_asset_info(symbol)

                return {
                    "info": info.model_dump(mode="json") if info else None,
                    "price": price.to_dict() if price else None,
                    "history": ([p.to_dict() for p in history] if history else []),
                }
            except Exception as e:
                logger.error(f"Market data fetch failed: {e}")
                return {"error": str(e)}

        async def get_fundamentals():
            return await fundamental_srv.get_fundamental_analysis(symbol)

        async def get_news():
            return await news_srv.fetch_latest_news(symbol, days_back)

        # 使用 asyncio.gather 并发执行
        import asyncio

        market_data, fundamentals, news = await asyncio.gather(
            get_market_data(), get_fundamentals(), get_news(), return_exceptions=True
        )

        return {
            "symbol": symbol,
            "timestamp": datetime.now().isoformat(),
            "market_data": (
                market_data
                if not isinstance(market_data, Exception)
                else {"error": str(market_data)}
            ),
            "fundamentals": (
                fundamentals
                if not isinstance(fundamentals, Exception)
                else {"error": str(fundamentals)}
            ),
            "news": news if not isinstance(news, Exception) else {"error": str(news)},
        }

    logger.info("✅ Registered 1 research tool for Nacos MCP")
