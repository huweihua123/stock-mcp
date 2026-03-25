# src/server/domain/services/news_service.py
"""Simple news search service.

The news module intentionally stays thin:
- stock-specific news is just a query builder on top of web search
- general news is a normalized web search result set
- Tavily is the single web-news backend
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from src.server.utils.logger import logger


class NewsService:
    """Thin news service aligned with DeerFlow-style web search tools."""

    def __init__(self, adapter_manager, cache, api_keys: dict, proxy_url: Optional[str] = None):
        self.adapter_manager = adapter_manager
        self.cache = cache
        self.logger = logger
        self.proxy_url = proxy_url
        self.client = httpx.AsyncClient(timeout=30, proxy=proxy_url, trust_env=False)
        self.tavily_api_key = api_keys.get("tavily")

    def _require_search_backend(self) -> None:
        if not self.tavily_api_key:
            raise RuntimeError("TAVILY_API_KEY not configured")

    def _build_stock_news_query(self, ticker: str) -> str:
        if ":" not in ticker:
            return f"{ticker} stock news"

        exchange, symbol = ticker.split(":", 1)
        exchange = exchange.upper()
        symbol = symbol.upper()

        if exchange in {"SSE", "SZSE", "BSE"}:
            return f"{symbol} 股票 新闻"
        if exchange == "HKEX":
            return f"{symbol} 港股 新闻"
        if exchange == "CRYPTO":
            return f"{symbol} crypto news"
        return f"{symbol} stock news"

    async def search_news(
        self,
        query: str,
        *,
        days_back: int = 7,
        max_results: int = 5,
    ) -> List[Dict[str, Any]]:
        """Run a normalized news search."""
        self._require_search_backend()

        cache_key = f"news_search:{query}:{days_back}:{max_results}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        payload = {
            "api_key": self.tavily_api_key,
            "query": query,
            "topic": "news",
            "days": days_back,
            "max_results": max_results,
        }

        response = await self.client.post("https://api.tavily.com/search", json=payload)
        response.raise_for_status()
        data = response.json()

        results = [
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "snippet": item.get("content"),
                "publish_time": item.get("published_date"),
                "source": item.get("source") or "Tavily",
            }
            for item in data.get("results", [])
        ]

        await self.cache.set(cache_key, results, ttl=300)
        return results

    async def web_search(self, query: str) -> List[Dict[str, Any]]:
        """Compatibility alias for MCP tools."""
        return await self.search_news(query)

    async def fetch_latest_news(
        self,
        ticker: str,
        days_back: int = 7,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """Get stock news through a stock-specific search query."""
        news = await self.search_news(
            self._build_stock_news_query(ticker),
            days_back=days_back,
            max_results=limit,
        )
        return {
            "ticker": ticker,
            "source": "tavily",
            "news": news,
            "timestamp": datetime.now().isoformat(),
        }

    async def get_breaking_news(self, limit: int = 10) -> List[Dict[str, Any]]:
        return await self.search_news(
            "breaking financial market news",
            days_back=3,
            max_results=limit,
        )

    async def get_financial_news(
        self,
        ticker: Optional[str] = None,
        sector: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        if ticker:
            query = self._build_stock_news_query(ticker)
        elif sector:
            query = f"{sector} sector financial news"
        else:
            query = "financial market news"
        return await self.search_news(query, days_back=7, max_results=limit)
