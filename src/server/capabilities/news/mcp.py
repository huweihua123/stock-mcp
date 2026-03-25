from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP

from src.server.capabilities.news.service import get_news_capability_service
from src.server.transports.mcp.artifacts import (
    ResourceVariant,
    create_artifact_envelope,
    create_artifact_response,
    create_mcp_error_result,
)
from src.server.runtime import get_runtime_context
from src.server.utils.logger import logger


def register_news_tools(mcp: FastMCP) -> None:
    runtime = get_runtime_context()
    service = get_news_capability_service(runtime)

    @mcp.tool(tags={"news-stock"})
    async def get_stock_news(symbol: str, days_back: int = 7, ctx: Context | None = None) -> dict[str, Any]:
        del ctx
        logger.info("Capability MCP: get_stock_news", symbol=symbol, days_back=days_back)
        result = await service.get_stock_news(symbol, days_back=days_back, limit=10)
        news_items = result.get("news", [])
        headlines = [item.get("title", "")[:30] for item in news_items[:3]]
        metadata = f"{symbol}近{days_back}天新闻共{len(news_items)}条: {'; '.join(headlines) or '暂无新闻'}..."
        return create_artifact_envelope(
            variant="news_citations",
            name=f"{symbol} 相关新闻",
            content={**result, "variant": "news_list"},
            description=metadata,
            visible_to_llm=False,
            display_in_report=True,
        )

    @mcp.tool(tags={"news-search"})
    async def get_latest_news(query: str, limit: int = 10, ctx: Context | None = None) -> dict[str, Any]:
        try:
            if ctx:
                await ctx.info(f"📰 搜索新闻: {query}", extra={"limit": limit})
            result = await service.search_news(query, days_back=7, limit=limit)
            items = result.get("items", [])
            if ctx:
                await ctx.info(f"✅ 新闻搜索完成: {len(items)}条结果")
            return create_artifact_envelope(
                variant="news_citations",
                name=f"新闻搜索: {query}",
                content={**result, "variant": "news_citations"},
                description=f"搜索 '{query}' 找到 {len(items)} 条新闻",
                visible_to_llm=False,
                display_in_report=True,
            )
        except Exception as exc:
            logger.error(f"Capability MCP error in get_latest_news: {exc}")
            if ctx:
                await ctx.error(f"❌ 新闻搜索失败: {query}", extra={"error": str(exc)})
            return create_mcp_error_result(str(exc))

    @mcp.tool(tags={"news-sentiment", "us-news"})
    async def get_us_news_sentiment(
        symbol: str,
        days_back: int = 3,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        if ctx:
            await ctx.info(f"📰 美股新闻情感分析: {symbol} ({days_back}d)")
        try:
            content = await service.get_us_news_sentiment(symbol, days_back=days_back)
            counts = content["counts"]
            summary = (
                f"{symbol} 近{days_back}天新闻情感: {content['overall_sentiment'].upper()} "
                f"(正面{counts['positive']}条/负面{counts['negative']}条/中性{counts['neutral']}条, "
                f"综合得分{content['composite_score']:+.2f})"
            )
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
        except Exception as exc:
            logger.error("Capability MCP error in get_us_news_sentiment", symbol=symbol, error=str(exc))
            summary = f"获取 {symbol} 新闻情感失败: {exc}"
            artifact = create_artifact_envelope(
                variant=ResourceVariant.US_NEWS_SENTIMENT,
                name=f"{symbol} 新闻情感",
                content={"error": str(exc)},
                description=summary,
                visible_to_llm=True,
            )
            return create_artifact_response(summary=summary, artifact=artifact)
