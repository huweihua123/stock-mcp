# src/server/domain/services/news_service.py
"""NewsService – fetches recent news using multiple providers.
Supports Tavily, Google Gemini, and DuckDuckGo fallback.
Returns structured data (JSON).
"""

import asyncio
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from src.server.utils.logger import logger


class NewsService:
    def __init__(self, adapter_manager, cache, api_keys: dict):
        self.adapter_manager = adapter_manager
        self.cache = cache
        self.logger = logger
        self.client = httpx.AsyncClient(timeout=30)

        self.tavily_api_key = api_keys.get("tavily")
        self.google_api_key = api_keys.get("google")
        self.web_search_provider = api_keys.get("web_search_provider", "tavily")

        self.logger.info(
            f"🔧 [NewsService] Tavily API Key: {'✅ Configured' if self.tavily_api_key else '❌ Not Configured'}"
        )

    async def _run(self, func, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))


    def _get_news_adapter_for_ticker(self, ticker: str):
        """Get adapter that supports news for the ticker."""
        if ":" not in ticker:
            return None
        exchange, symbol = ticker.split(":", 1)
        if exchange in ["NASDAQ", "NYSE", "AMEX"]:
            finnhub = self.adapter_manager.adapters.get("finnhub")
            if finnhub and hasattr(finnhub, "get_news"):
                return finnhub
        if exchange in ["SSE", "SZSE", "BSE", "HKEX"]:
            akshare = self.adapter_manager.adapters.get("akshare")
            if akshare and hasattr(akshare, "get_news"):
                return akshare
        return None

    async def _search_via_google_gemini(self, query: str) -> List[Dict[str, Any]]:
        """Use Google Gemini with search grounding."""
        if not self.google_api_key:
            raise ValueError("GOOGLE_API_KEY not configured")
        try:
            import google.generativeai as genai

            genai.configure(api_key=self.google_api_key)
            model = genai.GenerativeModel(
                "gemini-2.0-flash-exp", tools="google_search_retrieval"
            )
            response = await model.generate_content_async(query)

            # Parse Gemini response to structured format if possible
            # For now, just return the text as a single item
            return [
                {
                    "title": "Google Gemini Search Result",
                    "url": "",
                    "snippet": response.text,
                    "source": "Google Gemini",
                    "publish_time": datetime.now().isoformat(),
                }
            ]
        except Exception as e:
            self.logger.error(f"Google Gemini error: {e}")
            raise

    async def web_search(self, query: str) -> List[Dict[str, Any]]:
        """General web search."""
        cache_key = f"web_search_json:{hash(query)}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        results = []
        source_name = "Unknown"

        # Level 1: Tavily
        if self.tavily_api_key:
            try:
                self.logger.info(f"🔍 [Web Search] Using Tavily: {query}")
                raw_results = await self._fetch_via_tavily(query, days_back=7)
                if raw_results:
                    results = raw_results
                    source_name = "Tavily"
            except Exception as e:
                self.logger.warning(f"⚠️ [Web Search] Tavily failed: {e}")

        # Level 2: Google Gemini
        if not results and self.google_api_key:
            use_google = self.web_search_provider == "google"
            if use_google:
                try:
                    self.logger.info(f"🔍 [Web Search] Using Google Gemini: {query}")
                    results = await self._search_via_google_gemini(query)
                    source_name = "Google Gemini"
                except Exception as e:
                    self.logger.warning(f"⚠️ [Web Search] Google Gemini failed: {e}")

        # Level 3: DuckDuckGo
        if not results:
            try:
                self.logger.info(f"🔍 [Web Search] Using DuckDuckGo: {query}")
                results = await self._fetch_via_duckduckgo(query, days_back=7)
                if results:
                    source_name = "DuckDuckGo"
            except Exception as e:
                self.logger.error(f"❌ [Web Search] DuckDuckGo failed: {e}")

        if results:
            # Add source info if missing
            for r in results:
                if "source" not in r:
                    r["source"] = source_name

            await self.cache.set(cache_key, results, ttl=300)
            return results

        return []

    async def get_breaking_news(self) -> List[Dict[str, Any]]:
        """Get breaking news."""
        return await self.web_search("breaking news urgent updates today latest")

    async def get_financial_news(
        self, ticker: Optional[str] = None, sector: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get financial market news."""
        today = datetime.now().strftime("%Y-%m-%d")
        if ticker:
            search_query = f"{ticker} stock news financial {today}"
        elif sector:
            search_query = f"{sector} sector financial news {today}"
        else:
            search_query = f"financial market news {today}"

        return await self.web_search(search_query)

    async def _fetch_via_tavily(
        self, query: str, days_back: int
    ) -> List[Dict[str, Any]]:
        """Call Tavily search endpoint."""
        if not self.tavily_api_key:
            return []
        payload = {
            "api_key": self.tavily_api_key,
            "query": query,
            "topic": "news",
            "days": days_back,
        }
        try:
            url = "https://api.tavily.com/search"
            resp = await self.client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()

            results = []
            for item in data.get("results", []):
                results.append(
                    {
                        "title": item.get("title"),
                        "url": item.get("url"),
                        "snippet": item.get("content"),
                        "publish_time": item.get("published_date"),
                        "source": "Tavily",
                    }
                )
            return results
        except Exception as e:
            self.logger.error(f"Tavily error: {e}")
            return []

    async def _fetch_via_duckduckgo(
        self, query: str, days_back: int
    ) -> List[Dict[str, Any]]:
        """Fallback simple search using DuckDuckGo."""
        url = "https://html.duckduckgo.com/html/"
        params = {"q": f"{query} news", "kl": "us-en"}
        try:
            resp = await self.client.get(url, params=params)
            resp.raise_for_status()
            html = resp.text
            pattern = r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>([^<]+)</a>'
            matches = re.findall(pattern, html)
            results = []
            for link, title in matches[:10]:
                results.append(
                    {
                        "title": title.strip(),
                        "url": link,
                        "snippet": "",
                        "source": "DuckDuckGo",
                        "publish_time": datetime.now().isoformat(),
                    }
                )
            return results
        except Exception as e:
            self.logger.error(f"DDG error: {e}")
            return []

    async def fetch_latest_news(
        self, ticker: str, days_back: int = 30
    ) -> Dict[str, Any]:
        """Get latest news for a ticker."""
        std_ticker = await self.adapter_manager.resolve_ticker(ticker)
        cache_key = f"news_json:{std_ticker}:{days_back}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        results = []
        source_name = "Unknown"

        # Try adapter first
        news_adapter = self._get_news_adapter_for_ticker(std_ticker)
        if news_adapter:
            try:
                results = await news_adapter.get_news(std_ticker, limit=20)
                if results:
                    source_name = news_adapter.name
            except Exception as e:
                self.logger.error(f"Adapter news fetch failed: {e}")

        # Fallback to web search
        if not results:
            query = f"{ticker} stock news latest"
            results = await self.web_search(query)
            if results:
                source_name = "Web Search"

        response = {
            "ticker": std_ticker,
            "source": source_name,
            "news": results,
            "timestamp": datetime.now().isoformat(),
        }

        await self.cache.set(cache_key, response, ttl=3600)
        return response
