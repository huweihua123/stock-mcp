# src/server/domain/adapters/ccxt_adapter.py
"""CCXT Adapter for cryptocurrency data using exchange APIs.

Uses ccxt.async_support for asynchronous exchange interactions.
Provides high-quality OHLCV data suitable for technical analysis.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set

import ccxt.async_support as ccxt
from src.server.domain.adapters.base import BaseDataAdapter
from src.server.domain.types import (
    AdapterCapability,
    Asset,
    AssetPrice,
    AssetType,
    DataSource,
    Exchange,
)
from src.server.utils.logger import logger


class CCXTAdapter(BaseDataAdapter):
    name = "ccxt"

    def __init__(
        self,
        cache,
        default_exchange_id: str = "binance",
        proxy_url: Optional[str] = None,
    ):
        super().__init__(DataSource.CCXT)
        self.cache = cache
        self.default_exchange_id = default_exchange_id
        self.proxy_url = proxy_url
        self._exchange_instances: Dict[str, ccxt.Exchange] = {}
        self.logger = logger

    async def _get_exchange(self, exchange_id: str) -> ccxt.Exchange:
        """Get or create an exchange instance."""
        if exchange_id in self._exchange_instances:
            return self._exchange_instances[exchange_id]

        if exchange_id not in ccxt.exchanges:
            raise ValueError(f"Exchange {exchange_id} not supported by CCXT")

        exchange_class = getattr(ccxt, exchange_id)
        config = {
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        }
        if self.proxy_url:
            config["aiohttp_proxy"] = self.proxy_url

        exchange = exchange_class(config)
        
        # Load markets to ensure we can validate symbols
        await exchange.load_markets()
        
        self._exchange_instances[exchange_id] = exchange
        return exchange

    async def close(self):
        """Close all exchange connections."""
        for exchange in self._exchange_instances.values():
            await exchange.close()
        self._exchange_instances.clear()

    def get_capabilities(self) -> List[AdapterCapability]:
        """Declare CCXT adapter's capabilities."""
        return [
            AdapterCapability(asset_type=AssetType.CRYPTO, exchanges={Exchange.CRYPTO}),
        ]

    def convert_to_source_ticker(self, internal_ticker: str) -> str:
        """Convert CRYPTO:BTC-USD to BTC/USDT (defaulting to USDT for USD)."""
        # Format: CRYPTO:BTC-USD -> BTC/USDT
        if ":" in internal_ticker:
            symbol_part = internal_ticker.split(":")[1]
        else:
            symbol_part = internal_ticker

        # Replace - with /
        symbol = symbol_part.replace("-", "/")
        
        # If it's just BTC, assume BTC/USDT
        if "/" not in symbol:
            symbol = f"{symbol}/USDT"
            
        # Handle USD vs USDT mapping if needed
        if symbol.endswith("/USD"):
             symbol = symbol.replace("/USD", "/USDT")
             
        return symbol.upper()

    def convert_to_internal_ticker(
        self, source_ticker: str, default_exchange: Optional[str] = None
    ) -> str:
        """Convert BTC/USDT to CRYPTO:BTC-USD."""
        symbol = source_ticker.replace("/", "-")
        if symbol.endswith("-USDT"):
            symbol = symbol.replace("-USDT", "-USD")
        return f"CRYPTO:{symbol}"

    async def get_asset_info(self, ticker: str) -> Optional[Asset]:
        """Fetch asset info (limited support in CCXT, mainly market info)."""
        # CCXT is not great for asset metadata (description, website, etc.)
        # We rely on CryptoAdapter (CoinGecko) for that.
        # This implementation returns basic market info.
        
        exchange = await self._get_exchange(self.default_exchange_id)
        symbol = self.convert_to_source_ticker(ticker)
        
        if symbol not in exchange.markets:
            return None
            
        market = exchange.markets[symbol]
        
        return Asset(
            ticker=ticker,
            asset_type=AssetType.CRYPTO,
            name=market.get("base", "") + " " + market.get("quote", ""),
            market_info={
                "exchange": self.default_exchange_id.upper(),
                "currency": market.get("quote", "USD"),
                "market_status": "OPEN"
            },
            source_mappings={DataSource.CRYPTO: symbol},
            properties={
                "base": market.get("base"),
                "quote": market.get("quote"),
                "spot": market.get("spot"),
                "future": market.get("future"),
            }
        )

    async def get_real_time_price(self, ticker: str) -> Optional[AssetPrice]:
        """Fetch current price using CCXT fetch_ticker."""
        cache_key = f"ccxt:price:{ticker}"
        cached = await self.cache.get(cache_key)
        if cached:
            return AssetPrice.from_dict(cached)

        exchange = await self._get_exchange(self.default_exchange_id)
        symbol = self.convert_to_source_ticker(ticker)

        try:
            ticker_data = await exchange.fetch_ticker(symbol)
            
            price = AssetPrice(
                ticker=ticker,
                price=Decimal(str(ticker_data["last"])),
                currency="USD", # Assuming normalized to USD/USDT
                timestamp=datetime.now(timezone.utc),
                volume=Decimal(str(ticker_data.get("baseVolume", 0))),
                open_price=Decimal(str(ticker_data.get("open", 0))),
                high_price=Decimal(str(ticker_data.get("high", 0))),
                low_price=Decimal(str(ticker_data.get("low", 0))),
                close_price=Decimal(str(ticker_data.get("close", 0))),
                change=Decimal(str(ticker_data.get("change", 0))),
                change_percent=Decimal(str(ticker_data.get("percentage", 0))),
                source=DataSource.CRYPTO
            )
            
            await self.cache.set(cache_key, price.to_dict(), ttl=10) # Short TTL for real-time
            return price
            
        except Exception as e:
            self.logger.warning(f"CCXT fetch_ticker failed for {symbol}: {e}")
            return None

    async def get_historical_prices(
        self,
        ticker: str,
        start_date: datetime,
        end_date: datetime,
        interval: str = "1d",
    ) -> List[AssetPrice]:
        """Fetch OHLCV data using CCXT fetch_ohlcv."""
        # Map interval to CCXT timeframe
        timeframe_map = {
            "1d": "1d",
            "1h": "1h",
            "15m": "15m",
            "5m": "5m",
            "1m": "1m",
            "1wk": "1w",
            "1mo": "1M"
        }
        timeframe = timeframe_map.get(interval, "1d")
        
        start_ts = int(start_date.timestamp() * 1000)
        end_ts = int(end_date.timestamp() * 1000)
        
        cache_key = f"ccxt:history:{ticker}:{start_ts}:{end_ts}:{timeframe}"
        cached = await self.cache.get(cache_key)
        if cached:
            return [AssetPrice.from_dict(item) for item in cached]

        exchange = await self._get_exchange(self.default_exchange_id)
        symbol = self.convert_to_source_ticker(ticker)

        try:
            # fetch_ohlcv(symbol, timeframe, since, limit, params)
            # Note: limit might be needed if range is large, but for now simple fetch
            ohlcv = await exchange.fetch_ohlcv(symbol, timeframe, since=start_ts)
            
            prices = []
            for candle in ohlcv:
                # [timestamp, open, high, low, close, volume]
                ts, o, h, l, c, v = candle
                
                if ts > end_ts:
                    break
                    
                prices.append(AssetPrice(
                    ticker=ticker,
                    timestamp=datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
                    price=Decimal(str(c)),
                    open_price=Decimal(str(o)),
                    high_price=Decimal(str(h)),
                    low_price=Decimal(str(l)),
                    close_price=Decimal(str(c)),
                    volume=Decimal(str(v)),
                    currency="USD",
                    source=DataSource.CRYPTO
                ))
                
            await self.cache.set(cache_key, [p.to_dict() for p in prices], ttl=300)
            return prices

        except Exception as e:
            self.logger.error(f"CCXT fetch_ohlcv failed for {symbol}: {e}")
            return []
