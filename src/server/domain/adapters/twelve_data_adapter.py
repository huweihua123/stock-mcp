# src/server/domain/adapters/twelve_data_adapter.py
"""Twelve Data adapter for spot metals, FX, and equities."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
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


class TwelveDataAdapter(BaseDataAdapter):
    name = "twelve_data"

    def __init__(self, api_key: str, cache, proxy_url: str = None):
        super().__init__(DataSource.TWELVE_DATA)
        self.api_key = api_key
        self.cache = cache
        self.proxy_url = proxy_url
        self.base_url = "https://api.twelvedata.com"

    def get_capabilities(self) -> List[AdapterCapability]:
        return [
            AdapterCapability(asset_type=AssetType.COMMODITY_SPOT, exchanges={Exchange.OTC}),
            AdapterCapability(asset_type=AssetType.FX, exchanges={Exchange.FOREX}),
            AdapterCapability(asset_type=AssetType.STOCK, exchanges={Exchange.NASDAQ, Exchange.NYSE, Exchange.AMEX, Exchange.HKEX}),
        ]

    def convert_to_source_ticker(self, ticker: str) -> str:
        if ":" not in ticker:
            return ticker
        exchange, symbol = ticker.split(":", 1)
        exchange = exchange.upper()
        symbol = symbol.upper()

        # Spot metals / FX like XAGUSD -> XAG/USD
        if exchange in {"OTC", "FOREX"}:
            if len(symbol) == 6 and symbol.isalpha():
                return f"{symbol[:3]}/{symbol[3:]}"
            return symbol

        # US equities
        if exchange in {"NASDAQ", "NYSE", "AMEX"}:
            return symbol

        # HK equities
        if exchange == "HKEX":
            return f"{symbol}.HK"

        return symbol

    def convert_to_internal_ticker(
        self, source_ticker: str, default_exchange: Optional[str] = None
    ) -> str:
        raw = (source_ticker or "").strip().upper()
        if not raw:
            return raw
        if ":" in raw:
            return raw
        if "/" in raw:
            core = raw.replace("/", "")
            if core in {"XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD"}:
                return f"OTC:{core}"
            if len(core) == 6 and core.isalpha():
                return f"FOREX:{core}"
        if raw.endswith(".HK"):
            return f"HKEX:{raw.replace('.HK', '')}"
        if raw.endswith(".SS"):
            return f"SSE:{raw.replace('.SS', '')}"
        if raw.endswith(".SZ"):
            return f"SZSE:{raw.replace('.SZ', '')}"
        if default_exchange:
            return f"{default_exchange}:{raw}"
        return f"NASDAQ:{raw}"

    async def get_asset_info(self, ticker: str) -> Optional[Asset]:
        # Minimal asset info without external calls
        if ":" not in ticker:
            return None
        exchange, symbol = ticker.split(":", 1)
        exchange = exchange.upper()
        symbol = symbol.upper()

        if exchange == "OTC":
            name = "Spot"
            currency = "USD"
            asset_type = AssetType.COMMODITY_SPOT
        elif exchange == "FOREX":
            name = f"FX {symbol[:3]}/{symbol[3:]}"
            currency = symbol[3:]
            asset_type = AssetType.FX
        else:
            name = symbol
            currency = "USD"
            asset_type = AssetType.STOCK

        return Asset(
            ticker=ticker,
            asset_type=asset_type,
            name=name,
            market_info=MarketInfo(
                exchange=exchange,
                country="Global",
                currency=currency,
                timezone="UTC",
                market_status=MarketStatus.UNKNOWN,
            ),
            source_mappings={DataSource.TWELVE_DATA: self.convert_to_source_ticker(ticker)},
        )

    async def get_real_time_price(self, ticker: str) -> Optional[AssetPrice]:
        if not self.api_key:
            logger.warning("Twelve Data API key missing")
            return None

        ticker_norm = self.convert_to_source_ticker(ticker)
        cache_key = f"twelve_data:price:{ticker_norm}"
        cached = await self.cache.get(cache_key)
        if cached:
            return AssetPrice.from_dict(cached)

        params = {"symbol": ticker_norm, "apikey": self.api_key}
        url = f"{self.base_url}/price"

        try:
            timeout = httpx.Timeout(20.0)
            proxy = self.proxy_url
            async with httpx.AsyncClient(timeout=timeout, proxy=proxy, trust_env=False) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

            price_val = data.get("price")
            if price_val is None:
                return None

            asset_price = AssetPrice(
                ticker=ticker,
                price=Decimal(str(price_val)),
                currency="USD",
                timestamp=datetime.utcnow(),
                volume=None,
                source=DataSource.TWELVE_DATA,
            )
            await self.cache.set(cache_key, asset_price.to_dict(), ttl=30)
            return asset_price
        except Exception as e:
            logger.warning(f"Twelve Data price fetch failed for {ticker}: {e}")
            return None

    async def get_historical_prices(
        self,
        ticker: str,
        start_date: datetime,
        end_date: datetime,
        interval: str = "1d",
    ) -> List[AssetPrice]:
        if not self.api_key:
            logger.warning("Twelve Data API key missing")
            return []

        ticker_norm = self.convert_to_source_ticker(ticker)
        interval_norm = self._map_interval(interval)
        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")

        cache_key = f"twelve_data:history:{ticker_norm}:{start_str}:{end_str}:{interval_norm}"
        cached = await self.cache.get(cache_key)
        if cached:
            return [AssetPrice.from_dict(item) for item in cached]

        params = {
            "symbol": ticker_norm,
            "interval": interval_norm,
            "start_date": start_str,
            "end_date": end_str,
            "apikey": self.api_key,
            "timezone": "UTC",
        }
        url = f"{self.base_url}/time_series"

        try:
            timeout = httpx.Timeout(20.0)
            proxy = self.proxy_url
            async with httpx.AsyncClient(timeout=timeout, proxy=proxy, trust_env=False) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

            values = data.get("values") or data.get("data") or []
            results: List[AssetPrice] = []
            for row in values:
                dt = self._parse_datetime(row.get("datetime") or row.get("date"))
                if not dt:
                    continue
                close_price = self._safe_decimal(row.get("close"))
                open_price = self._safe_decimal(row.get("open"))
                high_price = self._safe_decimal(row.get("high"))
                low_price = self._safe_decimal(row.get("low"))
                volume = self._safe_decimal(row.get("volume"))
                if close_price is None:
                    continue
                results.append(
                    AssetPrice(
                        ticker=ticker,
                        price=close_price,
                        currency="USD",
                        timestamp=dt,
                        volume=volume,
                        open_price=open_price,
                        high_price=high_price,
                        low_price=low_price,
                        close_price=close_price,
                        source=DataSource.TWELVE_DATA,
                    )
                )

            results.sort(key=lambda x: x.timestamp)
            await self.cache.set(cache_key, [p.to_dict() for p in results], ttl=3600)
            return results
        except Exception as e:
            logger.warning(f"Twelve Data history fetch failed for {ticker}: {e}")
            return []

    async def get_real_time_price_by_provider_symbol(
        self, provider_symbol: str, internal_ticker: Optional[str] = None
    ) -> Optional[AssetPrice]:
        # Use provider_symbol directly when provided
        if internal_ticker:
            return await self.get_real_time_price(internal_ticker)
        return await self.get_real_time_price(provider_symbol)

    async def get_historical_prices_by_provider_symbol(
        self,
        provider_symbol: str,
        start_date: datetime,
        end_date: datetime,
        interval: str = "1d",
        internal_ticker: Optional[str] = None,
    ) -> List[AssetPrice]:
        if internal_ticker:
            # Prefer provider_symbol if it looks like a provider format
            ticker_norm = provider_symbol if provider_symbol else internal_ticker
        else:
            ticker_norm = provider_symbol
        return await self.get_historical_prices(ticker_norm, start_date, end_date, interval)

    def _map_interval(self, interval: str) -> str:
        mapping = {
            "1d": "1day",
            "1wk": "1week",
            "1mo": "1month",
        }
        return mapping.get(interval, interval)

    def _parse_datetime(self, value: Any) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except Exception:
            try:
                return datetime.strptime(str(value), "%Y-%m-%d")
            except Exception:
                return None

    def _safe_decimal(self, value: Any) -> Optional[Decimal]:
        try:
            if value is None or value == "":
                return None
            return Decimal(str(value))
        except Exception:
            return None
