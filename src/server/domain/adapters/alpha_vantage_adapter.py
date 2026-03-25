# src/server/domain/adapters/alpha_vantage_adapter.py
"""Alpha Vantage adapter for spot metals (XAU/XAG) and FX pairs.

Currently supports:
  - OTC:XAUUSD (Gold spot)
  - OTC:XAGUSD (Silver spot)
  - FOREX:XXXXXX (FX pairs, e.g. FOREX:EURUSD)
"""

from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import Any, Dict, List, Optional

import httpx

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


class AlphaVantageAdapter(BaseDataAdapter):
    name = "alpha_vantage"
    _supported_tickers = {"OTC:XAUUSD", "OTC:XAGUSD"}
    _fx_pattern = re.compile(r"^FOREX:([A-Z]{6})$")

    def __init__(self, api_key: str, cache, proxy_url: str = None):
        super().__init__(DataSource.ALPHA_VANTAGE)
        self.api_key = api_key
        self.cache = cache
        self.proxy_url = proxy_url
        self.base_url = "https://www.alphavantage.co/query"

    def get_capabilities(self) -> List[AdapterCapability]:
        return [
            AdapterCapability(
                asset_type=AssetType.COMMODITY_SPOT, exchanges={Exchange.OTC}
            ),
            AdapterCapability(asset_type=AssetType.FX, exchanges={Exchange.FOREX}),
        ]

    async def get_asset_info(self, ticker: str) -> Optional[Asset]:
        if ticker not in self._supported_tickers:
            match = self._fx_pattern.match(ticker)
            if not match:
                return None
            pair = match.group(1)
            return Asset(
                ticker=ticker,
                name=f"FX {pair[:3]}/{pair[3:]}",
                asset_type=AssetType.FX,
                exchange=Exchange.FOREX.value,
                currency=pair[3:],
                source=DataSource.ALPHA_VANTAGE,
                market_info=MarketInfo(
                    exchange=Exchange.FOREX.value,
                    country="Global",
                    currency=pair[3:],
                    timezone="UTC",
                ),
            )
        name = "Gold Spot" if ticker.endswith("XAUUSD") else "Silver Spot"
        return Asset(
            ticker=ticker,
            name=name,
            asset_type=AssetType.COMMODITY_SPOT,
            exchange=Exchange.OTC.value,
            currency="USD",
            source=DataSource.ALPHA_VANTAGE,
            market_info=MarketInfo(
                exchange=Exchange.OTC.value,
                country="US",
                currency="USD",
                timezone="UTC",
            ),
        )

    async def get_real_time_price(self, ticker: str) -> Optional[AssetPrice]:
        if ticker in self._supported_tickers:
            symbol = "XAU" if ticker.endswith("XAUUSD") else "XAG"
            params = {
                "function": "GOLD_SILVER_SPOT",
                "symbol": symbol,
                "apikey": self.api_key,
            }
            data = await self._fetch_json(params)
            price = _safe_float(data.get("price"))
            timestamp = _safe_datetime(data.get("timestamp"))
            if price is None:
                return None
            return AssetPrice(
                ticker=ticker,
                price=price,
                currency="USD",
                timestamp=timestamp,
                open_price=price,
                high_price=price,
                low_price=price,
                close_price=price,
                volume=None,
                source=DataSource.ALPHA_VANTAGE,
            )
        match = self._fx_pattern.match(ticker)
        if not match:
            return None
        pair = match.group(1)
        fx = await self._get_fx_daily(pair[:3], pair[3:], full=False)
        return fx[0] if fx else None

    async def get_historical_prices(
        self,
        ticker: str,
        start_date: datetime,
        end_date: datetime,
        interval: str = "1d",
    ) -> List[AssetPrice]:
        if ticker in self._supported_tickers:
            symbol = "XAU" if ticker.endswith("XAUUSD") else "XAG"
            params = {
                "function": "GOLD_SILVER_HISTORY",
                "symbol": symbol,
                "interval": "daily",
                "apikey": self.api_key,
            }

            cache_key = f"alpha_vantage:history:{ticker}:{start_date:%Y-%m-%d}:{end_date:%Y-%m-%d}:{interval}"
            cached = await self.cache.get(cache_key)
            if cached:
                return cached

            data = await self._fetch_json(params)
            rows = data.get("data", [])
            results: List[AssetPrice] = []
            for row in rows:
                dt = _safe_datetime(row.get("date"))
                if not dt:
                    continue
                if dt < start_date or dt > end_date:
                    continue
                price = _safe_float(row.get("price"))
                if price is None:
                    continue
                results.append(
                    AssetPrice(
                        ticker=ticker,
                        price=price,
                        currency="USD",
                        timestamp=dt,
                        open_price=price,
                        high_price=price,
                        low_price=price,
                        close_price=price,
                        volume=None,
                        source=DataSource.ALPHA_VANTAGE,
                    )
                )

            results.sort(key=lambda x: x.timestamp)
            await self.cache.set(cache_key, results, ttl=3600)
            return results

        match = self._fx_pattern.match(ticker)
        if not match:
            return []
        pair = match.group(1)
        results = await self._get_fx_daily(pair[:3], pair[3:], full=True)
        return [
            p
            for p in results
            if p.timestamp >= start_date and p.timestamp <= end_date
        ]

    def convert_to_source_ticker(self, ticker: str) -> str:
        return ticker

    def convert_to_internal_ticker(self, ticker: str) -> str:
        return ticker

    def validate_ticker(self, ticker: str) -> bool:
        if ticker in self._supported_tickers:
            return True
        return bool(self._fx_pattern.match(ticker))

    async def _get_fx_daily(
        self, from_symbol: str, to_symbol: str, full: bool
    ) -> List[AssetPrice]:
        params = {
            "function": "FX_DAILY",
            "from_symbol": from_symbol,
            "to_symbol": to_symbol,
            "outputsize": "full" if full else "compact",
            "apikey": self.api_key,
        }
        cache_key = (
            f"alpha_vantage:fx_daily:{from_symbol}{to_symbol}:{params['outputsize']}"
        )
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        data = await self._fetch_json(params)
        series = data.get("Time Series FX (Daily)", {})
        results: List[AssetPrice] = []
        for day, values in series.items():
            dt = _safe_datetime(day)
            if not dt:
                continue
            close_price = _safe_float(values.get("4. close"))
            open_price = _safe_float(values.get("1. open"))
            high_price = _safe_float(values.get("2. high"))
            low_price = _safe_float(values.get("3. low"))
            if close_price is None:
                continue
            results.append(
                AssetPrice(
                    ticker=f"FOREX:{from_symbol}{to_symbol}",
                    price=close_price,
                    currency=to_symbol,
                    timestamp=dt,
                    open_price=open_price,
                    high_price=high_price,
                    low_price=low_price,
                    close_price=close_price,
                    volume=None,
                    source=DataSource.ALPHA_VANTAGE,
                )
            )

        results.sort(key=lambda x: x.timestamp, reverse=True)
        await self.cache.set(cache_key, results, ttl=3600)
        return results

    async def _fetch_json(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if not self.api_key:
            logger.warning("Alpha Vantage API key missing")
            return {"error": "Alpha Vantage API key missing"}
        timeout = httpx.Timeout(20.0)
        proxy = None
        if self.proxy_url:
            proxy = self.proxy_url
        async with httpx.AsyncClient(timeout=timeout, proxy=proxy, trust_env=False) as client:
            resp = await client.get(self.base_url, params=params)
            resp.raise_for_status()
            return resp.json()


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _safe_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except Exception:
        try:
            return datetime.strptime(str(value), "%Y-%m-%d").replace(tzinfo=UTC)
        except Exception:
            return None
