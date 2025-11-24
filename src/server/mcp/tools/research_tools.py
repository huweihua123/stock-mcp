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
    def perform_deep_research(symbol: str, days_back: int = 30) -> Dict[str, Any]:
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

        def get_market_data():
            try:
                end = datetime.now()
                start = end - timedelta(days=365)

                price = manager.get_real_time_price(symbol)
                history = manager.get_historical_prices(symbol, start, end)
                info = manager.get_asset_info(symbol)

                return {
                    "info": info.model_dump(mode="json") if info else None,
                    "price": price.to_dict() if price else None,
                    "history": [p.to_dict() for p in history] if history else [],
                }
            except Exception as e:
                logger.error(f"Market data fetch failed: {e}")
                return {"error": str(e)}

        def get_fundamentals():
            return fundamental_srv.get_fundamental_analysis(symbol)

        def get_news():
            return news_srv.fetch_latest_news(symbol, days_back)

        def run_research():
            # 同步依次调用，不再使用 asyncio.gather
            market_data = get_market_data()
            fundamentals = get_fundamentals()
            news = get_news()

            return {
                "symbol": symbol,
                "timestamp": datetime.now().isoformat(),
                "market_data": market_data,
                "fundamentals": fundamentals,
                "news": news,
            }

        return run_research()

    logger.info("✅ Registered 1 research tool for Nacos MCP")
