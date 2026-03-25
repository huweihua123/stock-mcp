# src/server/mcp/tools/news_tools.py
"""MCP tools for news data.
Provides get_stock_news and search_news.
Returns structured data (JSON).
"""

from typing import Any, Dict, List, Literal, Optional

from fastmcp import FastMCP, Context

from src.server.core.dependencies import Container
from src.server.utils.logger import logger
from src.server.mcp.tools.artifact_utils import (
    ResourceVariant,
    create_artifact_envelope,
    create_mcp_error_result,
    create_artifact_response,
)


def register_news_tools(mcp: FastMCP):
    @mcp.tool(tags={"news-stock"})
    async def get_stock_news(
        symbol: str, days_back: int = 7, ctx: Context = None
    ) -> Dict[str, Any]:
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
        result["variant"] = "news_list"

        # 构建摘要 - 新闻数量和关键信息
        news_items = result.get("news", result.get("items", []))
        news_count = len(news_items)

        # 提取前几条新闻标题作为摘要
        headlines = [item.get("title", "")[:30] for item in news_items[:3]]
        headlines_str = "; ".join(headlines) if headlines else "暂无新闻"

        metadata = f"{symbol}近{days_back}天新闻共{news_count}条: {headlines_str}..."

        return create_artifact_envelope(
            variant="news_citations",
            name=f"{symbol} 相关新闻",
            content=result,
            description=metadata,
            visible_to_llm=False,  # 新闻列表数据量大
            display_in_report=True,
        )

    @mcp.tool(tags={"news-search"})
    async def get_latest_news(
        query: str, limit: int = 10, ctx: Context = None
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
            results = await service.search_news(query, max_results=limit)

            # Limit results
            if isinstance(results, list):
                results = results[:limit]

            count = len(results) if isinstance(results, list) else 0
            if ctx:
                await ctx.info(f"✅ 新闻搜索完成: {count}条结果")

            result = {"items": results, "variant": "news_citations"}

            description = f"搜索 '{query}' 找到 {count} 条新闻"

            return create_artifact_envelope(
                variant="news_citations",
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
            return create_mcp_error_result(str(e))

    # ------------------------------------------------------------------
    # get_us_news_sentiment  (对齐竞品: FinBERT风格情感评分)
    # ------------------------------------------------------------------
    @mcp.tool(tags={"news-sentiment", "us-news"})
    async def get_us_news_sentiment(
        symbol: str,
        days_back: int = 3,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Get news sentiment analysis for a US stock.

        Fetches recent English-language news headlines for the given US stock
        and scores each item with a rule-based sentiment classifier that mimics
        FinBERT outputs (positive / neutral / negative + confidence score).

        Useful for gauging market sentiment before earnings or catalysts.

        Args:
            symbol: US stock ticker. Format: EXCHANGE:SYMBOL
                Examples: NASDAQ:AAPL, NYSE:TSLA, NASDAQ:NVDA
            days_back: Days to look back for news (default 3)
            ctx: FastMCP Context

        Returns:
            ArtifactResponse with per-headline sentiment and aggregate score
        """
        if ctx:
            await ctx.info(f"📰 美股新闻情感分析: {symbol} ({days_back}d)")

        try:
            logger.info(
                "MCP tool: get_us_news_sentiment",
                symbol=symbol,
                days_back=days_back,
            )
            service = Container.news_service()

            # Extract pure ticker for search (e.g. NASDAQ:AAPL → AAPL)
            raw_ticker = symbol.split(":")[-1] if ":" in symbol else symbol

            # Fetch news headlines
            query = f"{raw_ticker} stock news"
            raw_news = await service.search_news(query, days_back=days_back, max_results=15)
            if not isinstance(raw_news, list):
                raw_news = []

            # ---- Lightweight rule-based sentiment scorer ----
            POSITIVE_WORDS = {
                "beat",
                "beats",
                "surge",
                "surges",
                "soar",
                "jumps",
                "rises",
                "rally",
                "rallies",
                "upgrade",
                "upgraded",
                "buy",
                "outperform",
                "record",
                "growth",
                "profit",
                "strong",
                "bullish",
                "positive",
                "exceed",
                "exceeds",
                "upside",
                "boosts",
                "boost",
                "gains",
                "optimistic",
                "raised",
                "raise",
                "higher",
                "breakout",
            }
            NEGATIVE_WORDS = {
                "miss",
                "misses",
                "drop",
                "drops",
                "falls",
                "decline",
                "declines",
                "downgrade",
                "downgraded",
                "sell",
                "underperform",
                "loss",
                "losses",
                "weak",
                "bearish",
                "negative",
                "below",
                "cut",
                "cuts",
                "lower",
                "warning",
                "concern",
                "risk",
                "lawsuit",
                "probe",
                "investigation",
                "recall",
                "delay",
                "disappoints",
                "disappointing",
                "slump",
            }

            def _score_headline(text: str):
                words = set(text.lower().split())
                pos = len(words & POSITIVE_WORDS)
                neg = len(words & NEGATIVE_WORDS)
                if pos > neg:
                    label = "positive"
                    score = min(0.5 + pos * 0.15, 0.95)
                elif neg > pos:
                    label = "negative"
                    score = min(0.5 + neg * 0.15, 0.95)
                else:
                    label = "neutral"
                    score = 0.5
                return label, round(score, 2)

            scored = []
            for item in raw_news[:15]:
                title = item.get("title") or item.get("headline") or ""
                snippet = item.get("snippet") or item.get("description") or ""
                text = f"{title} {snippet}"
                if not text.strip():
                    continue
                label, score = _score_headline(text)
                scored.append(
                    {
                        "title": title[:120],
                        "url": item.get("url") or item.get("link") or "",
                        "source": item.get("source") or "",
                        "sentiment": label,
                        "confidence": score,
                    }
                )

            # Aggregate
            pos_count = sum(1 for s in scored if s["sentiment"] == "positive")
            neg_count = sum(1 for s in scored if s["sentiment"] == "negative")
            neu_count = sum(1 for s in scored if s["sentiment"] == "neutral")
            total = len(scored) or 1
            avg_conf = sum(s["confidence"] for s in scored) / total

            # Composite sentiment score: +1 per positive, -1 per negative
            composite = (pos_count - neg_count) / total
            overall = (
                "positive"
                if composite > 0.1
                else "negative" if composite < -0.1 else "neutral"
            )

            summary = (
                f"{symbol} 近{days_back}天新闻情感: {overall.upper()} "
                f"(正面{pos_count}条/负面{neg_count}条/中性{neu_count}条, "
                f"综合得分{composite:+.2f})"
            )

            content = {
                "ticker": symbol,
                "days_back": days_back,
                "overall_sentiment": overall,
                "composite_score": round(composite, 3),
                "avg_confidence": round(avg_conf, 2),
                "counts": {
                    "positive": pos_count,
                    "negative": neg_count,
                    "neutral": neu_count,
                    "total": total,
                },
                "headlines": scored,
                "note": "Sentiment scored with rule-based classifier (FinBERT-style labels)",
            }

            artifact = create_artifact_envelope(
                variant=ResourceVariant.US_NEWS_SENTIMENT,
                name=f"{symbol} 新闻情感分析",
                content=content,
                description=summary,
                metadata={"ticker": symbol, "days_back": days_back},
                visible_to_llm=False,
                display_in_report=True,
            )
            return create_artifact_response(summary=summary, artifact=artifact)

        except Exception as e:
            logger.error("get_us_news_sentiment error", symbol=symbol, error=str(e))
            summary = f"获取 {symbol} 新闻情感失败: {e}"
            artifact = create_artifact_envelope(
                variant=ResourceVariant.US_NEWS_SENTIMENT,
                name=f"{symbol} 新闻情感",
                content={"error": str(e)},
                description=summary,
                visible_to_llm=True,
            )
            return create_artifact_response(summary=summary, artifact=artifact)

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
