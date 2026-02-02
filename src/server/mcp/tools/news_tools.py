# src/server/mcp/tools/news_tools.py
"""MCP tools for news data.
Provides get_stock_news and search_news.
Returns structured data (JSON).
"""

from typing import Any, Dict, List, Literal, Optional

from fastmcp import FastMCP, Context

from src.server.core.dependencies import Container
from src.server.utils.logger import logger
from src.server.mcp.tools.artifact_utils import create_artifact_envelope


def register_news_tools(mcp: FastMCP):
    @mcp.tool(tags={"news-stock"})
    async def get_stock_news(symbol: str, days_back: int = 7, ctx: Context = None) -> Dict[str, Any]:
        """Get professional stock news.

        Args:
            symbol: Asset ticker. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA
                - 加密货币: CRYPTO:BTC, CRYPTO:ETH
            days_back: Days to look back (default 7)

        Returns:
            ArtifactEnvelope containing news items
        """
        service = Container.news_service()
        logger.info("MCP tool: get_stock_news", symbol=symbol, days_back=days_back)
        result = await service.fetch_latest_news(symbol, days_back)
        result["component_type"] = "news_list"
        
        # 构建摘要 - 新闻数量和关键信息
        news_items = result.get("news", result.get("items", []))
        news_count = len(news_items)
        
        # 提取前几条新闻标题作为摘要
        headlines = [item.get("title", "")[:30] for item in news_items[:3]]
        headlines_str = "; ".join(headlines) if headlines else "暂无新闻"
        
        metadata = f"{symbol}近{days_back}天新闻共{news_count}条: {headlines_str}..."
        
        return create_artifact_envelope(
            component_type="news_citations",
            name=f"{symbol} 相关新闻",
            content=result,
            description=metadata,
            visible_to_llm=False,  # 新闻列表数据量大
            display_in_report=True,
        )

    @mcp.tool(tags={"news-search"})
    async def get_latest_news(
        query: str,
        limit: int = 10,
        ctx: Context = None
    ) -> Dict[str, Any]:
        """Get latest market news.

        Args:
            query: Search query (e.g. "technology stocks")
            limit: Max results (default 10)
            ctx: FastMCP Context for logging

        Returns:
            List of news items
        """
        service = Container.news_service()
        logger.info("MCP tool: get_latest_news", query=query, limit=limit)
        
        if ctx:
            await ctx.info(f"📰 搜索新闻: {query}", extra={"limit": limit})

        try:
            # Use web_search for general queries
            results = await service.web_search(query)
            
            # Limit results
            if isinstance(results, list):
                results = results[:limit]
            
            count = len(results) if isinstance(results, list) else 0
            if ctx:
                await ctx.info(f"✅ 新闻搜索完成: {count}条结果")
                
            result = {
                "items": results,
                "component_type": "news_citations"
            }
            
            description = f"搜索 '{query}' 找到 {count} 条新闻"
            
            return create_artifact_envelope(
                component_type="news_citations",
                name=f"新闻搜索: {query}",
                content=result,
                description=description,
                visible_to_llm=False,
                display_in_report=True,
            )
        except Exception as e:
            logger.error(f"Get latest news failed: {e}")
            if ctx:
                await ctx.error(f"❌ 新闻搜索失败: {query}", extra={"error": str(e)})
            return {"error": str(e)}


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
