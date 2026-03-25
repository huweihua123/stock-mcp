from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.server.capabilities.news.service import NewsCapabilityService


class _StubCache:
    async def get(self, key: str):
        del key
        return None

    async def set(self, key: str, value, ttl: int):
        del key, value, ttl
        return None


def _runtime():
    container = SimpleNamespace(
        cache=lambda: _StubCache(),
        proxy_url=lambda: None,
    )
    settings = SimpleNamespace(api_keys=SimpleNamespace(tavily="test-tavily-key"))
    return SimpleNamespace(container=container, settings=settings)


def test_news_capability_service_wraps_search_shape():
    service = NewsCapabilityService(_runtime())
    async def _fake_search(query: str, *, days_back: int, max_results: int):
        del days_back, max_results
        return [{"title": "macro update", "url": "https://example.com/macro", "source": "Example"}]
    service._search_news = _fake_search  # type: ignore[attr-defined]

    result = asyncio.run(service.search_news("macro", days_back=7, limit=5))

    assert result["query"] == "macro"
    assert len(result["items"]) == 1
    assert result["items"][0]["title"] == "macro update"


def test_news_capability_service_computes_sentiment_summary():
    service = NewsCapabilityService(_runtime())
    async def _fake_search(query: str, *, days_back: int, max_results: int):
        del days_back, max_results
        if "AAPL" in query:
            return [
                {
                    "title": "AAPL beats expectations and sees strong growth",
                    "snippet": "Analysts stay optimistic after profit surprise",
                    "url": "https://example.com/aapl",
                    "source": "Example",
                },
                {
                    "title": "AAPL faces lawsuit risk and weak outlook",
                    "snippet": "Investors weigh lower guidance",
                    "url": "https://example.com/aapl-risk",
                    "source": "Example",
                },
            ]
        return []
    service._search_news = _fake_search  # type: ignore[attr-defined]

    result = asyncio.run(service.get_us_news_sentiment("NASDAQ:AAPL", days_back=3))

    assert result["ticker"] == "NASDAQ:AAPL"
    assert result["counts"]["total"] == 2
    assert result["overall_sentiment"] in {"positive", "neutral", "negative"}
    assert len(result["headlines"]) == 2
