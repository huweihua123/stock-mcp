# src/server/domain/adapters/crypto_adapter.py
"""CryptoAdapter using CoinGecko API for cryptocurrency data.

All methods are async and use httpx for HTTP requests.
"""

import asyncio
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


class CryptoAdapter(BaseDataAdapter):
    name = "crypto"

    def __init__(self, cache, proxy_url: Optional[str] = None):
        super().__init__(DataSource.CRYPTO)
        self.cache = cache
        self.logger = logger
        self.proxy_url = proxy_url

    def get_capabilities(self) -> List[AdapterCapability]:
        """Declare Crypto adapter's capabilities."""
        return [
            AdapterCapability(asset_type=AssetType.CRYPTO, exchanges={Exchange.CRYPTO}),
        ]

    def convert_to_source_ticker(self, internal_ticker: str) -> str:
        """Convert CRYPTO:SYMBOL to CoinGecko ID format."""
        if ":" in internal_ticker:
            symbol = internal_ticker.split(":")[1].lower()
        else:
            symbol = internal_ticker.lower()

        # Map common symbols to CoinGecko IDs
        symbol_map = {
            "btc": "bitcoin",
            "eth": "ethereum",
            "usdt": "tether",
            "bnb": "binancecoin",
            "usdc": "usd-coin",
            "xrp": "ripple",
            "ada": "cardano",
            "doge": "dogecoin",
            "sol": "solana",
            "dot": "polkadot",
        }

        return symbol_map.get(symbol, symbol)

    def convert_to_internal_ticker(
        self, source_ticker: str, default_exchange: Optional[str] = None
    ) -> str:
        """Convert CoinGecko ID to CRYPTO:SYMBOL."""
        # Reverse map CoinGecko IDs to symbols
        id_map = {
            "bitcoin": "BTC",
            "ethereum": "ETH",
            "tether": "USDT",
            "binancecoin": "BNB",
            "usd-coin": "USDC",
            "ripple": "XRP",
            "cardano": "ADA",
            "dogecoin": "DOGE",
            "solana": "SOL",
            "polkadot": "DOT",
        }

        symbol = id_map.get(source_ticker.lower(), source_ticker.upper())
        return f"CRYPTO:{symbol}"

    async def _run(self, func, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    def _to_coingecko_id(self, ticker: str) -> str:
        return self.convert_to_source_ticker(ticker)

    async def get_asset_info(self, ticker: str) -> Optional[Asset]:
        """Fetch detailed asset information."""
        cache_key = f"crypto:info:{ticker}"
        cached = await self.cache.get(cache_key)
        if cached:
            return Asset.model_validate(cached)

        coin_id = self._to_coingecko_id(ticker)
        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}"

        try:
            async with httpx.AsyncClient(proxy=self.proxy_url, trust_env=False) as client:
                resp = await client.get(url, timeout=30)
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                data = resp.json()

            asset = Asset(
                ticker=ticker,
                asset_type=AssetType.CRYPTO,
                name=data.get("name", ticker),
                market_info=MarketInfo(
                    exchange="CRYPTO",
                    country="Global",
                    currency="USD",
                    timezone="UTC",
                    market_status=MarketStatus.OPEN,
                ),
                source_mappings={DataSource.CRYPTO: coin_id},
                properties={
                    "symbol": data.get("symbol", "").upper(),
                    "description": data.get("description", {}).get("en", ""),
                    "homepage": data.get("links", {}).get("homepage", [""])[0],
                    "market_cap_rank": data.get("market_cap_rank"),
                },
            )

            await self.cache.set(cache_key, asset.model_dump(), ttl=3600)
            return asset
        except Exception as e:
            self.logger.warning(f"Failed to fetch asset info for {ticker}: {e}")
            return None

    async def get_real_time_price(self, ticker: str) -> Optional[AssetPrice]:
        """Fetch current price."""
        cache_key = f"crypto:price:{ticker}"
        cached = await self.cache.get(cache_key)
        if cached:
            return AssetPrice.from_dict(cached)

        coin_id = self._to_coingecko_id(ticker)
        url = (
            f"https://api.coingecko.com/api/v3/simple/price"
            f"?ids={coin_id}&vs_currencies=usd&include_market_cap=true&include_24hr_vol=true&include_24hr_change=true"
        )

        try:
            async with httpx.AsyncClient(proxy=self.proxy_url, trust_env=False) as client:
                resp = await client.get(url, timeout=30)
                resp.raise_for_status()
                data = resp.json()

            if coin_id not in data:
                return None

            item = data[coin_id]

            asset_price = AssetPrice(
                ticker=ticker,
                price=Decimal(str(item["usd"])),
                currency="USD",
                timestamp=datetime.utcnow(),
                volume=Decimal(str(item.get("usd_24h_vol", 0))),
                change=None,  # Can calculate if we had prev close
                change_percent=Decimal(str(item.get("usd_24h_change", 0))),
                market_cap=Decimal(str(item.get("usd_market_cap", 0))),
                source=DataSource.CRYPTO,
            )

            await self.cache.set(cache_key, asset_price.to_dict(), ttl=30)
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
        """Fetch historical prices."""
        start_ts = int(start_date.timestamp())
        end_ts = int(end_date.timestamp())

        cache_key = f"crypto:history:{ticker}:{start_ts}:{end_ts}:{interval}"
        cached = await self.cache.get(cache_key)
        if cached:
            return [AssetPrice.from_dict(item) for item in cached]

        coin_id = self._to_coingecko_id(ticker)
        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart/range"
        params = {
            "vs_currency": "usd",
            "from": start_ts,
            "to": end_ts,
        }

        try:
            async with httpx.AsyncClient(proxy=self.proxy_url, trust_env=False) as client:
                resp = await client.get(url, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()

            prices = []
            # data["prices"] is list of [timestamp, price]
            # data["total_volumes"] is list of [timestamp, volume]
            # We assume they align or just use prices

            for ts, price_val in data.get("prices", []):
                timestamp = datetime.utcfromtimestamp(ts / 1000)

                price = AssetPrice(
                    ticker=ticker,
                    price=Decimal(str(price_val)),
                    currency="USD",
                    timestamp=timestamp,
                    volume=Decimal("0"),  # Could match with total_volumes
                    open_price=Decimal(
                        str(price_val)
                    ),  # OHLC not available in this endpoint
                    high_price=Decimal(str(price_val)),
                    low_price=Decimal(str(price_val)),
                    close_price=Decimal(str(price_val)),
                    source=DataSource.CRYPTO,
                )
                prices.append(price)

            await self.cache.set(cache_key, [p.to_dict() for p in prices], ttl=3600)
            return prices
        except Exception as e:
            self.logger.error(f"Failed to fetch history for {ticker}: {e}")
            return []
