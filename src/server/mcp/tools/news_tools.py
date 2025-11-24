# src/server/mcp/tools/news_tools.py
"""MCP tools for news data.
Provides get_stock_news and search_news.
Returns structured data (JSON).
"""

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
    def get_stock_news(symbol: str, days_back: int = 7) -> Dict[str, Any]:
        """Get professional stock news.

        Args:
            symbol: Stock symbol (e.g. AAPL, 600519)
            days_back: Days to look back (default 7)

        Returns:
            Dictionary containing news items
        """
        service = Container.news_service()
        logger.info("MCP tool: get_stock_news", symbol=symbol, days_back=days_back)
        return service.fetch_latest_news(symbol, days_back)

    @mcp.tool()
    def search_news(
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
            return service.web_search(query)
        elif news_type == "breaking":
            return service.get_breaking_news()
        elif news_type == "financial":
            return service.get_financial_news(ticker, sector)
        elif news_type == "stock":
            from datetime import datetime

            today = datetime.now().strftime("%Y-%m-%d")
            search_query = f"{ticker} stock news latest {today}"
            return service.web_search(search_query)
        elif news_type == "sector":
            search_query = f"{sector} sector industry news latest"
            return service.web_search(search_query)

        return [{"error": "Unsupported news_type"}]

    logger.info("✅ Registered 2 news tools for Nacos MCP")
