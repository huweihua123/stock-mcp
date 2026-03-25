from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from src.server.runtime.models import RuntimeContext


class NewsCapabilityService:
    def __init__(self, runtime: RuntimeContext):
        self._runtime = runtime
        self._cache = runtime.container.cache()
        self._proxy_url = runtime.container.proxy_url()
        self._client = httpx.AsyncClient(timeout=30, proxy=self._proxy_url, trust_env=False)
        self._tavily_api_key = runtime.settings.api_keys.tavily

    def _require_search_backend(self) -> None:
        if not self._tavily_api_key:
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

    async def _search_news(
        self,
        query: str,
        *,
        days_back: int = 7,
        max_results: int = 5,
    ) -> list[dict[str, Any]]:
        self._require_search_backend()

        cache_key = f"news_search:{query}:{days_back}:{max_results}"
        cached = await self._cache.get(cache_key)
        if cached:
            return cached

        payload = {
            "api_key": self._tavily_api_key,
            "query": query,
            "topic": "news",
            "days": days_back,
            "max_results": max_results,
        }

        response = await self._client.post("https://api.tavily.com/search", json=payload)
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
        await self._cache.set(cache_key, results, ttl=300)
        return results

    async def get_stock_news(self, symbol: str, *, days_back: int, limit: int) -> dict[str, Any]:
        news = await self._search_news(
            self._build_stock_news_query(symbol),
            days_back=days_back,
            max_results=limit,
        )
        return {
            "ticker": symbol,
            "source": "tavily",
            "news": news,
            "timestamp": datetime.now().isoformat(),
        }

    async def search_news(self, query: str, *, days_back: int, limit: int) -> dict[str, Any]:
        items = await self._search_news(query, days_back=days_back, max_results=limit)
        return {"query": query, "items": items}

    async def get_us_news_sentiment(self, symbol: str, *, days_back: int) -> dict[str, Any]:
        raw_ticker = symbol.split(":")[-1] if ":" in symbol else symbol
        query = f"{raw_ticker} stock news"
        raw_news = await self._search_news(query, days_back=days_back, max_results=15)
        if not isinstance(raw_news, list):
            raw_news = []

        positive_words = {
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
        negative_words = {
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

        def score_headline(text: str) -> tuple[str, float]:
            words = set(text.lower().split())
            pos = len(words & positive_words)
            neg = len(words & negative_words)
            if pos > neg:
                return "positive", round(min(0.5 + pos * 0.15, 0.95), 2)
            if neg > pos:
                return "negative", round(min(0.5 + neg * 0.15, 0.95), 2)
            return "neutral", 0.5

        headlines = []
        for item in raw_news[:15]:
            title = item.get("title") or item.get("headline") or ""
            snippet = item.get("snippet") or item.get("description") or ""
            text = f"{title} {snippet}".strip()
            if not text:
                continue
            sentiment, confidence = score_headline(text)
            headlines.append(
                {
                    "title": title[:120],
                    "url": item.get("url") or item.get("link") or "",
                    "source": item.get("source") or "",
                    "sentiment": sentiment,
                    "confidence": confidence,
                }
            )

        pos_count = sum(1 for item in headlines if item["sentiment"] == "positive")
        neg_count = sum(1 for item in headlines if item["sentiment"] == "negative")
        neu_count = sum(1 for item in headlines if item["sentiment"] == "neutral")
        total = len(headlines) or 1
        avg_conf = sum(item["confidence"] for item in headlines) / total
        composite = (pos_count - neg_count) / total
        overall = "positive" if composite > 0.1 else "negative" if composite < -0.1 else "neutral"

        return {
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
            "headlines": headlines,
            "note": "Sentiment scored with rule-based classifier (FinBERT-style labels)",
        }


def get_news_capability_service(runtime: RuntimeContext) -> NewsCapabilityService:
    return NewsCapabilityService(runtime)
