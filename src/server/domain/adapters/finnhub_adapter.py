# src/server/domain/adapters/finnhub_adapter.py
"""FinnHub adapter for US stock news and data.

Provides news data for US stocks using FinnHub API.
All methods are async via asyncio.run_in_executor to avoid blocking
the event loop.
"""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from src.server.domain.adapters.base import BaseDataAdapter
from src.server.domain.types import (
    AdapterCapability,
    Asset,
    AssetPrice,
    AssetType,
    DataSource,
    Exchange,
    MarketInfo,
    MarketStatus,
)
from src.server.utils.logger import logger


class FinnhubAdapter(BaseDataAdapter):
    """FinnHub adapter for US market news data."""

    name = "finnhub"

    def __init__(self, finnhub_conn, cache):
        super().__init__(DataSource.FINNHUB)
        self.finnhub_conn = finnhub_conn
        self.cache = cache
        self.logger = logger

    def get_capabilities(self) -> List[AdapterCapability]:
        """Declare FinnHub's capabilities - US exchanges only."""
        return [
            AdapterCapability(
                asset_type=AssetType.STOCK,
                exchanges={Exchange.NASDAQ, Exchange.NYSE, Exchange.AMEX},
            ),
        ]

    def convert_to_source_ticker(self, internal_ticker: str) -> str:
        """Convert EXCHANGE:SYMBOL to FinnHub format."""
        if ":" in internal_ticker:
            _, symbol = internal_ticker.split(":", 1)
            return symbol
        return internal_ticker

    def convert_to_internal_ticker(
        self, source_ticker: str, default_exchange: Optional[str] = None
    ) -> str:
        """Convert FinnHub format to EXCHANGE:SYMBOL."""
        if ":" in source_ticker:
            return source_ticker

        exchange = default_exchange or "NASDAQ"
        return f"{exchange}:{source_ticker}"

    async def _run(self, func, *args, **kwargs):
        """Run sync function in executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    async def get_asset_info(self, ticker: str) -> Optional[Asset]:
        """Fetch asset info using Finnhub company profile."""
        cache_key = f"finnhub:info:{ticker}"
        cached = await self.cache.get(cache_key)
        if cached:
            return Asset.model_validate(cached)

        symbol = self.convert_to_source_ticker(ticker)
        session = self.finnhub_conn.get_client()
        if not session:
            return None

        base_url = self.finnhub_conn.get_base_url()
        api_key = self.finnhub_conn.get_api_key()

        try:

            def fetch_profile():
                url = f"{base_url}/stock/profile2"
                params = {"symbol": symbol, "token": api_key}
                resp = session.get(url, params=params, timeout=10)
                if resp.status_code == 200:
                    return resp.json()
                return {}

            profile = await self._run(fetch_profile)
            if not profile:
                return None

            asset = Asset(
                ticker=ticker,
                asset_type=AssetType.STOCK,
                name=profile.get("name", ticker),
                market_info=MarketInfo(
                    exchange=profile.get("exchange", "US"),
                    country=profile.get("country", "US"),
                    currency=profile.get("currency", "USD"),
                    timezone="UTC",
                    market_status=MarketStatus.UNKNOWN,
                ),
                source_mappings={DataSource.FINNHUB: symbol},
                properties={
                    "industry": profile.get("finnhubIndustry"),
                    "ipo": profile.get("ipo"),
                    "weburl": profile.get("weburl"),
                    "market_cap": profile.get("marketCapitalization"),
                },
            )

            await self.cache.set(cache_key, asset.model_dump(), ttl=3600)
            return asset
        except Exception as e:
            self.logger.warning(f"Failed to fetch asset info for {ticker}: {e}")
            return None

    async def get_real_time_price(self, ticker: str) -> Optional[AssetPrice]:
        """Fetch current price (Quote)."""
        cache_key = f"finnhub:price:{ticker}"
        cached = await self.cache.get(cache_key)
        if cached:
            return AssetPrice.from_dict(cached)

        symbol = self.convert_to_source_ticker(ticker)
        session = self.finnhub_conn.get_client()
        if not session:
            return None

        base_url = self.finnhub_conn.get_base_url()
        api_key = self.finnhub_conn.get_api_key()

        try:

            def fetch_quote():
                url = f"{base_url}/quote"
                params = {"symbol": symbol, "token": api_key}
                resp = session.get(url, params=params, timeout=10)
                if resp.status_code == 200:
                    return resp.json()
                return {}

            quote = await self._run(fetch_quote)
            if not quote or "c" not in quote:
                return None

            # Quote data: c (current), d (change), dp (percent), h, l, o, pc (prev close), t (timestamp)
            timestamp = datetime.fromtimestamp(
                quote.get("t", datetime.utcnow().timestamp())
            )

            asset_price = AssetPrice(
                ticker=ticker,
                price=Decimal(str(quote["c"])),
                currency="USD",  # Assumption
                timestamp=timestamp,
                volume=None,  # Not in quote endpoint
                open_price=Decimal(str(quote["o"])) if quote.get("o") else None,
                high_price=Decimal(str(quote["h"])) if quote.get("h") else None,
                low_price=Decimal(str(quote["l"])) if quote.get("l") else None,
                close_price=Decimal(str(quote["pc"])) if quote.get("pc") else None,
                change=Decimal(str(quote["d"])) if quote.get("d") else None,
                change_percent=Decimal(str(quote["dp"])) if quote.get("dp") else None,
                source=DataSource.FINNHUB,
            )

            await self.cache.set(cache_key, asset_price.to_dict(), ttl=60)
            return asset_price
        except Exception as e:
            self.logger.warning(f"Failed to fetch price for {ticker}: {e}")
            return None

    async def get_historical_prices(
        self,
        ticker: str,
        start_date: datetime,
        end_date: datetime,
        interval: str = "1d",
    ) -> List[AssetPrice]:
        """Fetch historical prices (Candles)."""
        # Finnhub candles: /stock/candle?symbol=...&resolution=D&from=...&to=...
        # Resolution: 1, 5, 15, 30, 60, D, W, M
        res_map = {"1d": "D", "1w": "W", "1m": "M"}
        res = res_map.get(interval, "D")

        symbol = self.convert_to_source_ticker(ticker)
        session = self.finnhub_conn.get_client()
        if not session:
            self.logger.warning("Finnhub session not available (missing API key?)")
            return []

        base_url = self.finnhub_conn.get_base_url()
        api_key = self.finnhub_conn.get_api_key()

        start_ts = int(start_date.timestamp())
        end_ts = int(end_date.timestamp())

        try:

            def fetch_candles():
                url = f"{base_url}/stock/candle"
                params = {
                    "symbol": symbol,
                    "resolution": res,
                    "from": start_ts,
                    "to": end_ts,
                    "token": api_key,
                }
                self.logger.debug(f"Finnhub request: {url} params={params}")
                resp = session.get(url, params=params, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    self.logger.debug(f"Finnhub response: {data}")
                    return data
                self.logger.warning(f"Finnhub error: {resp.status_code} {resp.text}")
                return {}

            data = await self._run(fetch_candles)
            if not data or data.get("s") != "ok":
                return []

            # Data: c, h, l, o, s, t, v
            prices = []
            for i in range(len(data["t"])):
                timestamp = datetime.fromtimestamp(data["t"][i])
                price = AssetPrice(
                    ticker=ticker,
                    price=Decimal(str(data["c"][i])),
                    currency="USD",
                    timestamp=timestamp,
                    volume=Decimal(str(data["v"][i])),
                    open_price=Decimal(str(data["o"][i])),
                    high_price=Decimal(str(data["h"][i])),
                    low_price=Decimal(str(data["l"][i])),
                    close_price=Decimal(str(data["c"][i])),
                    source=DataSource.FINNHUB,
                )
                prices.append(price)

            return prices
        except Exception as e:
            self.logger.error(f"Failed to fetch history for {ticker}: {e}")
            return []

    async def get_filings(
        self,
        ticker: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 10,
        filing_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch filings from Finnhub."""
        symbol = self.convert_to_source_ticker(ticker)
        session = self.finnhub_conn.get_client()
        if not session:
            return []

        base_url = self.finnhub_conn.get_base_url()
        api_key = self.finnhub_conn.get_api_key()

        try:

            def fetch_filings():
                url = f"{base_url}/stock/filings"
                params = {"symbol": symbol, "token": api_key}
                if start_date:
                    params["from"] = start_date.strftime("%Y-%m-%d")
                if end_date:
                    params["to"] = end_date.strftime("%Y-%m-%d")

                resp = session.get(url, params=params, timeout=10)
                if resp.status_code == 200:
                    return resp.json()
                return []

            data = await self._run(fetch_filings)
            if not data:
                return []

            filings = []
            for item in data:
                # Filter by filing types if provided
                if filing_types:
                    form = item.get("form", "")
                    if form not in filing_types:
                        continue

                accession_number = item.get("accessionNumber")
                filing_url = item.get("filingUrl")
                
                # Fallback: extract accessionNumber from URL if missing
                if not accession_number and filing_url:
                    import re
                    # Pattern matches standard SEC accession number format in URL
                    # e.g. .../0001104659-25-115949-index.html
                    match = re.search(r"(\d{10}-\d{2}-\d{6})", filing_url)
                    if match:
                        accession_number = match.group(1)

                filings.append(
                    {
                        "accessionNumber": accession_number,
                        "symbol": item.get("symbol"),
                        "filingDate": item.get("filedDate"),
                        "reportDate": item.get("reportDate"),
                        "form": item.get("form"),
                        "filingUrl": filing_url,
                        "description": item.get("description"),
                    }
                )
                
                if len(filings) >= limit:
                    break
            return filings
        except Exception as e:
            self.logger.error(f"Failed to fetch filings: {e}")
            return []

    async def get_news(self, ticker: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Fetch news from FinnHub API."""
        # Keep existing implementation
        cache_key = f"finnhub:news:{ticker}:{limit}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        session = self.finnhub_conn.get_client()
        if session is None:
            raise RuntimeError("FinnHub connection not established")

        symbol = self.convert_to_source_ticker(ticker)
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)

        def fetch_finnhub_news():
            base_url = self.finnhub_conn.get_base_url()
            api_key = self.finnhub_conn.get_api_key()
            url = f"{base_url}/company-news"
            params = {
                "symbol": symbol,
                "from": start_date.strftime("%Y-%m-%d"),
                "to": end_date.strftime("%Y-%m-%d"),
                "token": api_key,
            }
            response = session.get(url, params=params, timeout=10)
            if response.status_code == 200:
                return response.json()
            return []

        try:
            data = await self._run(fetch_finnhub_news)
            if not data:
                return []

            news_list = []
            for item in data[:limit]:
                try:
                    timestamp = item.get("datetime", 0)
                    publish_time = datetime.fromtimestamp(timestamp).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    news_list.append(
                        {
                            "title": item.get("headline", ""),
                            "url": item.get("url", ""),
                            "publish_time": publish_time,
                            "source": item.get("source", "FinnHub"),
                            "snippet": item.get("summary", "")[:200],
                        }
                    )
                except Exception:
                    continue

            await self.cache.set(cache_key, news_list, ttl=3600)
            return news_list
        except Exception as e:
            self.logger.error(f"Failed to fetch news: {e}")
            return []

    async def get_financials(self, ticker: str) -> Dict[str, Any]:
        """Fetch financial data using Finnhub API."""
        # Keep existing implementation
        cache_key = f"finnhub:financials:{ticker}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        symbol = self.convert_to_source_ticker(ticker)
        session = self.finnhub_conn.get_client()
        if session is None:
            raise RuntimeError("Finnhub connection not established")

        base_url = self.finnhub_conn.get_base_url()
        api_key = self.finnhub_conn.get_api_key()

        try:

            def fetch_finnhub_data():
                profile_url = f"{base_url}/stock/profile2"
                profile_params = {"symbol": symbol, "token": api_key}
                profile_resp = session.get(
                    profile_url, params=profile_params, timeout=10
                )
                profile = profile_resp.json() if profile_resp.status_code == 200 else {}

                metrics_url = f"{base_url}/stock/metric"
                metrics_params = {"symbol": symbol, "metric": "all", "token": api_key}
                metrics_resp = session.get(
                    metrics_url, params=metrics_params, timeout=10
                )
                metrics = metrics_resp.json() if metrics_resp.status_code == 200 else {}

                return profile, metrics

            profile, metrics = await self._run(fetch_finnhub_data)
            metric_data = metrics.get("metric", {})

            company_info = {
                "公司名称": profile.get("name", ""),
                "股票代码": symbol,
                "行业": profile.get("finnhubIndustry", ""),
                "国家": profile.get("country", ""),
                "网站": profile.get("weburl", ""),
                "总市值": profile.get("marketCapitalization", 0) * 1_000_000,
                "员工人数": profile.get("shareOutstanding", 0),
                "上市时间": profile.get("ipo", ""),
                "交易所": profile.get("exchange", ""),
            }

            result = {
                "balance_sheet": None,
                "income_statement": None,
                "cash_flow": None,
                "financial_indicators": None,
                "company_info": company_info,
                "_raw_info": {
                    "marketCap": profile.get("marketCapitalization", 0) * 1_000_000,
                    "peRatio": metric_data.get("peNormalizedAnnual", 0),
                    "pbRatio": metric_data.get("pbAnnual", 0),
                    "dividendYield": metric_data.get("dividendYieldIndicatedAnnual", 0),
                    "roe": metric_data.get("roeTTM", 0),
                    "roa": metric_data.get("roaTTM", 0),
                    "currentRatio": metric_data.get("currentRatioAnnual", 0),
                    "quickRatio": metric_data.get("quickRatioAnnual", 0),
                    "debtEquity": metric_data.get("totalDebt/totalEquityAnnual", 0),
                    "revenueGrowth": metric_data.get("revenueGrowthTTMYoy", 0),
                    "epsGrowth": metric_data.get("epsGrowthTTMYoy", 0),
                },
            }

            await self.cache.set(cache_key, result, ttl=3600)
            return result

        except Exception as e:
            self.logger.error(f"Failed to fetch financials for {ticker}: {e}")
            raise ValueError(f"Failed to fetch financials for {ticker}: {e}")
