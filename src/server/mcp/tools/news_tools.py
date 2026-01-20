# src/server/mcp/tools/news_tools.py
"""MCP tools for news data.
Provides get_stock_news and search_news.
Returns structured data (JSON).
"""

from typing import Any, Dict, List, Literal, Optional

from fastmcp import FastMCP

from src.server.core.dependencies import Container
from src.server.utils.logger import logger


def register_news_tools(mcp: FastMCP):
    @mcp.tool(tags={"news-stock"})
    async def get_stock_news(symbol: str, days_back: int = 7) -> Dict[str, Any]:
        """Get professional stock news.

        Args:
            symbol: Asset ticker. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA
                - 加密货币: CRYPTO:BTC, CRYPTO:ETH
            days_back: Days to look back (default 7)

        Returns:
            Dictionary containing news items
        """
        service = Container.news_service()
        logger.info("MCP tool: get_stock_news", symbol=symbol, days_back=days_back)
        return await service.fetch_latest_news(symbol, days_back)

    # @mcp.tool(tags={"news-search"})
    # async def search_news(
    #     query: Optional[str] = None,
    #     news_type: Literal[
    #         "general", "breaking", "financial", "stock", "sector"
    #     ] = "general",
    #     ticker: Optional[str] = None,
    #     sector: Optional[str] = None,
    # ) -> List[Dict[str, Any]]:
    #     """Flexible news search tool.

    #     Args:
    #         query: Custom search query (required for news_type="general")
    #         news_type: Type of news (general, breaking, financial, stock, sector)
    #         ticker: Stock ticker (for financial/stock type)
    #         sector: Industry sector (for financial/sector type)

    #     Returns:
    #         List of news items
    #     """
    #     service = Container.news_service()

    #     # Validate parameters
    #     if news_type == "general" and not query:
    #         return [{"error": "Query required for general news"}]

    #     if news_type == "stock" and not ticker:
    #         return [{"error": "Ticker required for stock news"}]

    #     if news_type == "sector" and not sector:
    #         return [{"error": "Sector required for sector news"}]

    #     logger.info(
    #         "MCP tool: search_news",
    #         query=query,
    #         news_type=news_type,
    #         ticker=ticker,
    #         sector=sector,
    #     )

    #     if news_type == "general":
    #         return await service.web_search(query)

    #     elif news_type == "breaking":
    #         return await service.get_breaking_news()

    #     elif news_type == "financial":
    #         return await service.get_financial_news(ticker, sector)

    #     elif news_type == "stock":
    #         from datetime import datetime
    #         today = datetime.now().strftime("%Y-%m-%d")
    #         search_query = f"{ticker} stock news latest {today}"
    #         return await service.web_search(search_query)

    #     elif news_type == "sector":
    #         search_query = f"{sector} sector industry news latest"
    #         return await service.web_search(search_query)

    #     return [{"error": "Unsupported news_type"}]
