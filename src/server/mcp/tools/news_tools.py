"""
Author: weihua hu
Date: 2025-11-25 01:44:44
LastEditTime: 2025-11-25 14:39:49
LastEditors: weihua hu
Description:
"""

# src/server/mcp/tools/news_tools.py
"""MCP tools for news data.
Provides get_stock_news and search_news.
Returns structured data (JSON).
"""

import time
from typing import Any, Dict, List, Literal, Optional

from nacos_mcp_wrapper.server.nacos_mcp import NacosMCP

from src.server.core.dependencies import Container
from src.server.utils.logger import logger


def register_news_tools(mcp: NacosMCP):
    """Register news-related tools for Nacos MCP.

    Args:
        mcp: NacosMCP instance
    """

    @mcp.tool()
    async def get_stock_news(symbol: str, days_back: int = 7) -> Dict[str, Any]:
        """Get professional stock news.

        Args:
            symbol: Stock symbol (e.g. AAPL, 600519)
            days_back: Days to look back (default 7)

        Returns:
            Dictionary containing news items
        """
        start_time = time.time()

        logger.info("=" * 70)
        logger.info("🔧 [get_stock_news] 工具调用开始")
        logger.info("=" * 70)
        logger.info(f"参数: symbol={symbol}, days_back={days_back}")

        try:
            service = Container.news_service()
            logger.info("✅ NewsService 实例已获取")

            logger.info(f"📞 调用 fetch_latest_news({symbol}, {days_back})")
            result = await service.fetch_latest_news(symbol, days_back)

            elapsed = (time.time() - start_time) * 1000
            logger.info("-" * 70)
            logger.info("✅ [get_stock_news] 执行成功")
            logger.info(f"耗时: {elapsed:.2f}ms")
            logger.info(f"结果类型: {type(result)}")
            if isinstance(result, dict):
                logger.info(f"结果键: {list(result.keys())}")
            logger.info("=" * 70)

            return result

        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            logger.error("=" * 70)
            logger.error("❌ [get_stock_news] 执行失败")
            logger.error(f"耗时: {elapsed:.2f}ms")
            logger.error(f"错误类型: {type(e).__name__}")
            logger.error(f"错误信息: {str(e)}")
            logger.error("=" * 70)
            raise

    @mcp.tool()
    async def search_news(
        query: Optional[str] = None,
        news_type: Literal[
            "general", "breaking", "financial", "stock", "sector"
        ] = "general",
        ticker: Optional[str] = None,
        sector: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Flexible news search tool.

        Args:
            query: Custom search query (required for news_type="general")
            news_type: Type of news (general, breaking, financial,
                stock, sector)
            ticker: Stock ticker (for financial/stock type)
            sector: Industry sector (for financial/sector type)

        Returns:
            List of news items
        """
        service = Container.news_service()

        if news_type == "general" and not query:
            return [{"error": "Query required for general news"}]
        if news_type == "stock" and not ticker:
            return [{"error": "Ticker required for stock news"}]
        if news_type == "sector" and not sector:
            return [{"error": "Sector required for sector news"}]

        logger.info(
            "MCP tool: search_news",
            query=query,
            news_type=news_type,
            ticker=ticker,
            sector=sector,
        )

        if news_type == "general":
            return await service.web_search(query)
        elif news_type == "breaking":
            return await service.get_breaking_news()
        elif news_type == "financial":
            return await service.get_financial_news(ticker, sector)
        elif news_type == "stock":
            from datetime import datetime

            today = datetime.now().strftime("%Y-%m-%d")
            search_query = f"{ticker} stock news latest {today}"
            return await service.web_search(search_query)
        elif news_type == "sector":
            search_query = f"{sector} sector industry news latest"
            return await service.web_search(search_query)

        return [{"error": "Unsupported news_type"}]

    logger.info("✅ Registered 2 news tools for Nacos MCP")
