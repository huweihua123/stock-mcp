# src/server/domain/adapters/futures_adapter.py
"""Futures adapter using Yahoo Finance (yfinance).

Focuses on commodity futures (COMEX/NYMEX/CME/ICE) with Yahoo-style symbols.
Examples:
- COMEX:SI -> SI=F
- NYMEX:CL -> CL=F
- ICE:BRN -> BRN=F
"""

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

import yfinance as yf

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


class FuturesAdapter(BaseDataAdapter):
    name = "futures_yahoo"

    def __init__(self, cache, proxy_url: str = None):
        super().__init__(DataSource.FUTURES)
        self.cache = cache
        self.logger = logger
        self.proxy_url = proxy_url

        if self.proxy_url:
            try:
                proxy_dict = {
                    "http": self.proxy_url,
                    "https": self.proxy_url,
                }
                yf.config.network.proxy = proxy_dict
                self.logger.info(
                    f"✅ Futures adapter configured with proxy: {self.proxy_url}"
                )
            except Exception as e:
                self.logger.warning(f"⚠️  Failed to set proxy via yf.config: {e}")
        else:
            self.logger.info("ℹ️  Futures adapter running without proxy")

    def get_capabilities(self) -> List[AdapterCapability]:
        return [
            AdapterCapability(
                asset_type=AssetType.COMMODITY_FUTURE,
                exchanges={Exchange.COMEX, Exchange.NYMEX, Exchange.CME, Exchange.ICE},
            )
        ]

    def convert_to_source_ticker(self, internal_ticker: str) -> str:
        # Internal: EXCHANGE:SYMBOL -> Yahoo: SYMBOL=F
        if ":" not in internal_ticker:
            return internal_ticker
        _, symbol = internal_ticker.split(":", 1)
        symbol = symbol.upper()
        if symbol.endswith("=F"):
            return symbol
        return f"{symbol}=F"

    def convert_to_internal_ticker(
        self, source_ticker: str, default_exchange: Optional[str] = None
    ) -> str:
        # Yahoo: GC=F -> COMEX:GC (best effort)
        core = source_ticker.replace("=F", "").upper()
        comex = {"GC", "SI", "HG"}
        nymex = {"CL", "NG"}
        ice = {"BRN"}
        if core in comex:
            return f"COMEX:{core}"
        if core in nymex:
            return f"NYMEX:{core}"
        if core in ice:
            return f"ICE:{core}"
        if default_exchange:
            return f"{default_exchange}:{core}"
        return f"CME:{core}"

    async def _run(self, func, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    async def get_asset_info(self, ticker: str) -> Optional[Asset]:
        source = self.convert_to_source_ticker(ticker)
        cache_key = f"futures:info:{source}"
        cached = await self.cache.get(cache_key)
        if cached:
            return Asset.model_validate(cached)

        try:
            ticker_obj = await self._run(yf.Ticker, source)
            info = await self._run(lambda: ticker_obj.info)
            if not info or not isinstance(info, dict):
                return None
            exchange = ticker.split(":", 1)[0] if ":" in ticker else "CME"
            asset = Asset(
                ticker=ticker,
                asset_type=AssetType.COMMODITY_FUTURE,
                name=info.get("longName") or info.get("shortName") or ticker,
                market_info=MarketInfo(
                    exchange=exchange,
                    country=info.get("country", "US"),
                    currency=info.get("currency", "USD"),
                    timezone=info.get("timeZoneShortName", "UTC"),
                    market_status=MarketStatus.UNKNOWN,
                ),
                source_mappings={DataSource.FUTURES: source},
                properties={
                    "symbol": info.get("symbol"),
                    "quoteType": info.get("quoteType"),
                    "market": info.get("market"),
                },
            )
            await self.cache.set(cache_key, asset.model_dump(), ttl=3600)
            return asset
        except Exception as e:
            self.logger.warning(f"Futures get_asset_info failed for {ticker}: {e}")
            return None

    async def get_real_time_price(self, ticker: str) -> Optional[AssetPrice]:
        source = self.convert_to_source_ticker(ticker)
        cache_key = f"futures:price:{source}"
        cached = await self.cache.get(cache_key)
        if cached:
            return AssetPrice.from_dict(cached)

        try:
            ticker_obj = await self._run(yf.Ticker, source)
            data = await self._run(lambda: ticker_obj.history(period="1d", interval="1m"))
            if data is None or data.empty:
                return None
            last = data.iloc[-1]
            price = AssetPrice(
                ticker=ticker,
                price=Decimal(str(last["Close"])),
                currency="USD",
                timestamp=last.name.to_pydatetime(),
                volume=Decimal(str(last["Volume"])) if "Volume" in last else None,
                open_price=Decimal(str(last["Open"])),
                high_price=Decimal(str(last["High"])),
                low_price=Decimal(str(last["Low"])),
                close_price=Decimal(str(last["Close"])),
                source=DataSource.FUTURES,
            )
            await self.cache.set(cache_key, price.to_dict(), ttl=10)
            return price
        except Exception as e:
            self.logger.warning(f"Futures price failed for {ticker}: {e}")
            return None

    async def get_historical_prices(
        self,
        ticker: str,
        start_date: datetime,
        end_date: datetime,
        interval: str = "1d",
    ) -> List[AssetPrice]:
        source = self.convert_to_source_ticker(ticker)
        cache_key = f"futures:history:{source}:{start_date}:{end_date}:{interval}"
        cached = await self.cache.get(cache_key)
        if cached:
            return [AssetPrice.from_dict(item) for item in cached]

        try:
            ticker_obj = await self._run(yf.Ticker, source)
            hist = await self._run(
                lambda: ticker_obj.history(
                    start=start_date.strftime("%Y-%m-%d"),
                    end=end_date.strftime("%Y-%m-%d"),
                    interval=interval,
                )
            )
            if hist is None or hist.empty:
                return []
            prices: List[AssetPrice] = []
            for ts, row in hist.iterrows():
                prices.append(
                    AssetPrice(
                        ticker=ticker,
                        price=Decimal(str(row["Close"])),
                        currency="USD",
                        timestamp=ts.to_pydatetime(),
                        volume=Decimal(str(row["Volume"])) if "Volume" in row else None,
                        open_price=Decimal(str(row["Open"])),
                        high_price=Decimal(str(row["High"])),
                        low_price=Decimal(str(row["Low"])),
                        close_price=Decimal(str(row["Close"])),
                        source=DataSource.FUTURES,
                    )
                )
            await self.cache.set(cache_key, [p.to_dict() for p in prices], ttl=3600)
            return prices
        except Exception as e:
            self.logger.warning(f"Futures history failed for {ticker}: {e}")
            return []
